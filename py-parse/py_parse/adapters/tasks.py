"""Celery task: a thin adapter that triggers the `run_parse_task` use case.

The actual parse orchestration lives in `domain.parse_task` (business logic);
this module only wires the concrete dependencies (built via `factory`) and
registers the task with Celery. The gRPC server never imports this module.
"""
from __future__ import annotations

from ..config import Settings
from ..domain.parse_task import run_parse_task
from ..factory import build_parser, build_registry, build_store
from .celery_app import celery_app


@celery_app.task(name="py_parse.parse_document", bind=True)
def parse_document(self, task_id: str, doc_id: str, source: str) -> None:
    settings = Settings.from_env()
    registry = build_registry(settings)
    parser = build_parser(settings)
    store = build_store(settings)
    run_parse_task(task_id, doc_id, source, registry, parser, store)
