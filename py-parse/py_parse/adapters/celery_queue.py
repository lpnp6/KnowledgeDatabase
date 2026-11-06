"""Celery-backed `TaskQueue` adapter.

Implements the `TaskQueue` port by enqueuing a `parse_document` task. The
caller-assigned `task_id` is also used as Celery's own task id, so task state is
traceable end-to-end under one identifier.
"""
from __future__ import annotations

from ..ports.task_queue import TaskQueue
from .tasks import parse_document


class CeleryTaskQueue(TaskQueue):
    def enqueue(self, task_id: str, doc_id: str, source: str) -> None:
        parse_document.apply_async(args=[task_id, doc_id, source], task_id=task_id)
