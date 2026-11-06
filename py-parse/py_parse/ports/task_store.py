"""Task persistence port.

Abstracts "store a parse task's status and result" behind an interface. The
hot status path is memory-level (Redis, TTL'd, the polling entry point); the
durable source of truth for final results is a database (PostgreSQL). A single
`TaskStore` implementation bridges both with a defined write ordering (durable
store first, then the hot cache) so Redis TTL expiry degrades gracefully to the
database.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

from ..domain.models import Chunk, DocMeta


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class TaskSnapshot:
    """A point-in-time view of a task, for the polling endpoint."""

    task_id: str
    status: TaskStatus
    error: str = ""
    doc_meta: DocMeta | None = None
    chunks: list[Chunk] = field(default_factory=list)


class TaskStore(ABC):
    """Store task status + result across the hot cache and durable store."""

    @abstractmethod
    def create_pending(self, task_id: str, doc_id: str, source: str) -> None:
        """Record a new task as PENDING (before it is enqueued)."""
        raise NotImplementedError

    @abstractmethod
    def set_running(self, task_id: str) -> None:
        """Mark a task RUNNING (worker picked it up)."""
        raise NotImplementedError

    @abstractmethod
    def set_failed(self, task_id: str, error: str) -> None:
        """Mark a task FAILED with an error message."""
        raise NotImplementedError

    @abstractmethod
    def save_result(
        self, task_id: str, doc_id: str, source: str, doc_meta: DocMeta, chunks: list[Chunk]
    ) -> None:
        """Persist a successful parse result (durable first, then hot cache)."""
        raise NotImplementedError

    @abstractmethod
    def get_snapshot(self, task_id: str) -> TaskSnapshot | None:
        """Return the current snapshot, or None if the task is unknown."""
        raise NotImplementedError
