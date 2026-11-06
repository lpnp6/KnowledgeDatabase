"""Task queue port.

Abstracts "enqueue a parse task for a worker to execute" behind an interface.
The concrete queue (Celery, RQ, a cloud queue, …) is a replaceable adapter; the
gRPC server only depends on this port, never on a specific queue engine.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class TaskQueue(ABC):
    """Submit parse tasks to a worker pool."""

    @abstractmethod
    def enqueue(self, task_id: str, doc_id: str, source: str) -> None:
        """Enqueue a parse task for execution. `task_id` is the caller-assigned
        business id (also used as the queue's own task id for traceability)."""
        raise NotImplementedError
