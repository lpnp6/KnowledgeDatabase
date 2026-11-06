"""gRPC server exposing `parse.v1.ParseService` (async task model).

The server is a thin control plane:

    SubmitParse  →  validate scheme, create PENDING task, enqueue via TaskQueue
    GetResult    →  poll the TaskStore (Redis hot path, PostgreSQL fallback)

The heavy read→extract→chunk work runs in a separate Celery worker; this process
only enqueues and polls.
"""
from __future__ import annotations

import logging
import uuid
from concurrent import futures

import grpc

from .adapters.celery_queue import CeleryTaskQueue
from .adapters.registry import SourceReaderRegistry
from .config import Settings
from .domain.models import Chunk
from .factory import build_registry, build_store
from .generated import (
    ChunkMetadata,
    DocMeta,
    GetResultRequest,
    GetResultResponse,
    ParseChunk,
    ParseRequest,
    ParseResult,
    ParseServiceServicer,
    SubmitParseResponse,
    TaskStatus as PbTaskStatus,
    add_ParseServiceServicer_to_server,
)
from .ports.source_reader import UnsupportedSchemeError
from .ports.task_queue import TaskQueue
from .ports.task_store import TaskSnapshot, TaskStatus as StoreStatus, TaskStore

logger = logging.getLogger("py_parse.grpc")


_STATUS_TO_PB = {
    StoreStatus.PENDING: PbTaskStatus.TASK_STATUS_PENDING,
    StoreStatus.RUNNING: PbTaskStatus.TASK_STATUS_RUNNING,
    StoreStatus.SUCCESS: PbTaskStatus.TASK_STATUS_SUCCESS,
    StoreStatus.FAILED: PbTaskStatus.TASK_STATUS_FAILED,
}


def _to_pb_chunk(chunk: Chunk) -> ParseChunk:
    return ParseChunk(
        chunk_id=chunk.chunk_id,
        text=chunk.text,
        metadata=ChunkMetadata(
            page=chunk.metadata.page,
            heading=chunk.metadata.heading,
            chunk_type=chunk.metadata.chunk_type,
        ),
    )


class ParserServicer(ParseServiceServicer):
    """Async submit/poll implementation of `ParseService`."""

    def __init__(
        self,
        registry: SourceReaderRegistry,
        store: TaskStore,
        queue: TaskQueue,
    ) -> None:
        self._registry = registry
        self._store = store
        self._queue = queue

    def SubmitParse(self, request: ParseRequest, context: grpc.ServicerContext) -> SubmitParseResponse:
        doc_id = request.doc_id
        source = request.source
        logger.info("SubmitParse: doc_id=%r source=%r", doc_id, source)

        # Cheap scheme validation up front (the actual read happens in the worker).
        try:
            self._registry.resolve(source)
        except UnsupportedSchemeError as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
            return

        task_id = uuid.uuid4().hex
        try:
            self._store.create_pending(task_id, doc_id, source)
            self._queue.enqueue(task_id, doc_id, source)
        except Exception as e:  # noqa: BLE001 - enqueue/storage failure is a server error
            logger.exception("failed to enqueue task for doc_id=%r", doc_id)
            context.abort(grpc.StatusCode.INTERNAL, f"failed to enqueue task: {e}")
            return

        logger.info("SubmitParse enqueued: task_id=%r", task_id)
        return SubmitParseResponse(task_id=task_id)

    def GetResult(self, request: GetResultRequest, context: grpc.ServicerContext) -> GetResultResponse:
        task_id = request.task_id
        snapshot = self._store.get_snapshot(task_id)
        if snapshot is None:
            context.abort(grpc.StatusCode.NOT_FOUND, f"unknown task_id: {task_id!r}")
            return

        return _snapshot_to_response(snapshot)


def _snapshot_to_response(snapshot: TaskSnapshot) -> GetResultResponse:
    resp = GetResultResponse(
        task_id=snapshot.task_id,
        status=_STATUS_TO_PB[snapshot.status],
    )
    if snapshot.status == StoreStatus.FAILED:
        resp.error = snapshot.error
    elif snapshot.status == StoreStatus.SUCCESS:
        resp.chunks.extend(_to_pb_chunk(c) for c in snapshot.chunks)
        if snapshot.doc_meta is not None:
            resp.result.CopyFrom(
                ParseResult(
                    chunk_count=len(snapshot.chunks),
                    doc_meta=DocMeta(
                        total_pages=snapshot.doc_meta.total_pages,
                        title=snapshot.doc_meta.title,
                        warning=snapshot.doc_meta.warning,
                    ),
                )
            )
    return resp


def build_server(settings: Settings) -> grpc.Server:
    """Create a configured-but-not-started gRPC server (health + ParseService)."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=settings.grpc_max_workers))

    registry = build_registry(settings)
    store = build_store(settings)
    queue = CeleryTaskQueue()

    add_ParseServiceServicer_to_server(ParserServicer(registry, store, queue), server)

    # Standard gRPC health service (grpc-health-probe compatible).
    try:
        from grpc_health.v1 import health, health_pb2, health_pb2_grpc

        health_servicer = health.HealthServicer()
        health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
        # "" sets the overall status (what an unnamed HealthCheckRequest probes);
        # the named entry lets callers pin the check to ParseService specifically.
        health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
        health_servicer.set("parse.v1.ParseService", health_pb2.HealthCheckResponse.SERVING)
    except ImportError:
        logger.warning("grpc_health not available; health service not registered")

    return server


def serve(settings: Settings) -> None:
    """Run the gRPC server, blocking until SIGTERM."""
    server = build_server(settings)
    address = f"{settings.grpc_host}:{settings.grpc_port}"
    server.add_insecure_port(address)
    server.start()
    logger.info("ParseService listening on %s (mineru=%s)", address, settings.mineru_url)
    server.wait_for_termination()
