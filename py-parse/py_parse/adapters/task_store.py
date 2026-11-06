"""Redis + PostgreSQL `TaskStore` implementation.

Redis is the hot status cache (the polling entry point, TTL'd); PostgreSQL is
the durable source of truth for final results. The write ordering is deliberate:

    durable (PostgreSQL) first  →  hot cache (Redis) second

so a `status=success` in Redis can never precede the durable result — if the
Redis keys expire (TTL), polling falls back to PostgreSQL.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

import redis as redis_lib
from sqlalchemy.orm import Session

from ..domain.models import Chunk, ChunkMetadata, DocMeta
from ..ports.task_store import TaskSnapshot, TaskStatus, TaskStore
from .db import ChunkRow, TaskRow

_TASK_KEY = "pyparse:task:{task_id}"
_RESULT_KEY = "pyparse:result:{task_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chunk_to_dict(c: Chunk) -> dict:
    return {
        "chunk_id": c.chunk_id,
        "text": c.text,
        "page": c.metadata.page,
        "heading": c.metadata.heading,
        "chunk_type": c.metadata.chunk_type,
    }


def _chunk_from_dict(d: dict) -> Chunk:
    return Chunk(
        chunk_id=d["chunk_id"],
        text=d["text"],
        metadata=ChunkMetadata(
            page=int(d.get("page", 0)),
            heading=d.get("heading", ""),
            chunk_type=d.get("chunk_type", "text"),
        ),
    )


def _doc_meta_to_dict(m: DocMeta) -> dict:
    return {"total_pages": m.total_pages, "title": m.title, "warning": m.warning}


def _doc_meta_from_dict(d: dict) -> DocMeta:
    return DocMeta(
        total_pages=int(d.get("total_pages", 0)),
        title=d.get("title", ""),
        warning=d.get("warning", ""),
    )


class RedisPostgresTaskStore(TaskStore):
    """Bridges a Redis hot cache and a PostgreSQL durable store."""

    def __init__(self, redis_client: redis_lib.Redis, session_factory, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._sessions = session_factory
        self._ttl = ttl_seconds

    # ---- writes (durable first, then hot cache) ----

    def create_pending(self, task_id: str, doc_id: str, source: str) -> None:
        now = _now()
        # Durable row first so the fallback always has the task.
        with self._sessions() as s:
            s.add(
                TaskRow(
                    task_id=task_id, doc_id=doc_id, source=source,
                    status=TaskStatus.PENDING.value, error=None,
                )
            )
            s.commit()
        self._redis.hset(
            _TASK_KEY.format(task_id=task_id),
            mapping={
                "status": TaskStatus.PENDING.value,
                "doc_id": doc_id,
                "source": source,
                "error": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        self._redis.expire(_TASK_KEY.format(task_id=task_id), self._ttl)

    def set_running(self, task_id: str) -> None:
        self._redis.hset(
            _TASK_KEY.format(task_id=task_id),
            mapping={"status": TaskStatus.RUNNING.value, "updated_at": _now()},
        )

    def set_failed(self, task_id: str, error: str) -> None:
        with self._sessions() as s:
            task = s.get(TaskRow, task_id)
            if task is not None:
                task.status = TaskStatus.FAILED.value
                task.error = error
                s.commit()
        self._redis.hset(
            _TASK_KEY.format(task_id=task_id),
            mapping={
                "status": TaskStatus.FAILED.value,
                "error": error,
                "updated_at": _now(),
            },
        )

    def save_result(
        self, task_id: str, doc_id: str, source: str, doc_meta: DocMeta, chunks: list[Chunk]
    ) -> None:
        # 1. Durable store: upsert task + replace chunks, then commit.
        with self._sessions() as s:
            task = s.get(TaskRow, task_id)
            if task is None:
                task = TaskRow(
                    task_id=task_id, doc_id=doc_id, source=source,
                    status=TaskStatus.SUCCESS.value, error=None,
                )
                s.add(task)
            task.status = TaskStatus.SUCCESS.value
            task.error = None
            task.total_pages = doc_meta.total_pages
            task.title = doc_meta.title
            task.warning = doc_meta.warning
            # Replace any prior chunks (idempotent on retry).
            s.query(ChunkRow).filter(ChunkRow.task_id == task_id).delete()
            for seq, c in enumerate(chunks):
                s.add(
                    ChunkRow(
                        task_id=task_id, seq=seq, chunk_id=c.chunk_id, text=c.text,
                        page=c.metadata.page, heading=c.metadata.heading,
                        chunk_type=c.metadata.chunk_type,
                    )
                )
            s.commit()

        # 2. Hot cache: only after the durable commit succeeded.
        result_json = json.dumps(
            {
                "doc_meta": _doc_meta_to_dict(doc_meta),
                "chunks": [_chunk_to_dict(c) for c in chunks],
            },
            ensure_ascii=False,
        )
        self._redis.set(_RESULT_KEY.format(task_id=task_id), result_json, ex=self._ttl)
        self._redis.hset(
            _TASK_KEY.format(task_id=task_id),
            mapping={"status": TaskStatus.SUCCESS.value, "updated_at": _now()},
        )

    # ---- read (hot cache, then durable fallback) ----

    def get_snapshot(self, task_id: str) -> TaskSnapshot | None:
        task_key = _TASK_KEY.format(task_id=task_id)
        raw = self._redis.hgetall(task_key)
        if raw:
            status = TaskStatus(raw.get("status", TaskStatus.PENDING.value))
            if status == TaskStatus.SUCCESS:
                result_raw = self._redis.get(_RESULT_KEY.format(task_id=task_id))
                if result_raw:
                    payload = json.loads(result_raw)
                    return TaskSnapshot(
                        task_id=task_id,
                        status=status,
                        doc_meta=_doc_meta_from_dict(payload["doc_meta"]),
                        chunks=[_chunk_from_dict(c) for c in payload["chunks"]],
                    )
                # status=success but result expired → fall through to DB.
            else:
                return TaskSnapshot(
                    task_id=task_id,
                    status=status,
                    error=raw.get("error", ""),
                )

        # Redis miss (TTL expired) → durable fallback.
        return self._snapshot_from_db(task_id)

    def _snapshot_from_db(self, task_id: str) -> TaskSnapshot | None:
        with self._sessions() as s:
            task = s.get(TaskRow, task_id)
            if task is None:
                return None
            status = TaskStatus(task.status)
            if status != TaskStatus.SUCCESS:
                return TaskSnapshot(task_id=task_id, status=status, error=task.error or "")
            chunks = [
                _chunk_from_dict(
                    {
                        "chunk_id": c.chunk_id, "text": c.text, "page": c.page,
                        "heading": c.heading, "chunk_type": c.chunk_type,
                    }
                )
                for c in s.query(ChunkRow)
                .filter(ChunkRow.task_id == task_id)
                .order_by(ChunkRow.seq)
                .all()
            ]
            doc_meta = DocMeta(
                total_pages=task.total_pages or 0,
                title=task.title or "",
                warning=task.warning or "",
            )
            return TaskSnapshot(task_id=task_id, status=status, doc_meta=doc_meta, chunks=chunks)
