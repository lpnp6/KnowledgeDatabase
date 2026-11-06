"""Celery application factory (infrastructure adapter).

The broker and result backend are both Redis (status is additionally mirrored
into Redis + PostgreSQL by the task store; the Celery result backend is only
used for Celery's own task tracking).

Start a worker with:
    celery -A py_parse.adapters.celery_app worker --loglevel=info -c 2
"""
from __future__ import annotations

from celery import Celery

from ..config import Settings


def make_celery(settings: Settings) -> Celery:
    app = Celery(
        "py_parse",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        task_track_started=True,
        include=["py_parse.adapters.tasks"],
    )
    return app


# Module-level app: `celery -A` imports this module and finds `celery_app`.
celery_app = make_celery(Settings.from_env())
