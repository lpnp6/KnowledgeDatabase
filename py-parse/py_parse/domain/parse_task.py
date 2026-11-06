"""Parse-task use case: the read → extract → chunk → persist orchestration.

This is the business logic of "parse a document for a task" — what a worker
actually does. It depends only on ports (SourceReader, DocumentExtractor,
TaskStore) and the domain parser, never on concrete adapters. The trigger (a
Celery task) and the concrete dependencies are supplied by the caller.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..ports.document_extractor import DocumentExtractError
from ..ports.source_reader import SourceReadError, UnsupportedSchemeError
from ..ports.task_store import TaskStore
from .parser import DocumentParser

if TYPE_CHECKING:
    from ..adapters.registry import SourceReaderRegistry


def run_parse_task(
    task_id: str,
    doc_id: str,
    source: str,
    registry: "SourceReaderRegistry",
    parser: DocumentParser,
    store: TaskStore,
) -> None:
    """Execute one parse task end-to-end.

    Marks the task RUNNING, reads the source, extracts, chunks, and persists the
    result. On a domain error it records the failure and re-raises (so the queue
    engine sees the task as failed too).
    """
    store.set_running(task_id)
    try:
        reader = registry.resolve(source)   # raises UnsupportedSchemeError
        stream = reader.read(source)        # raises SourceReadError
        with stream:
            output = parser.parse(stream, doc_id=doc_id)   # raises DocumentExtractError
        store.save_result(task_id, doc_id, source, output.doc_meta, output.chunks)
    except (UnsupportedSchemeError, SourceReadError, DocumentExtractError) as e:
        store.set_failed(task_id, str(e))
        raise
