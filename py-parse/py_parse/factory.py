"""Wiring helpers shared by the gRPC server and the Celery worker.

Both processes need the same graph (storage registry, extractor, parser, task
store), but the worker must not import gRPC. Keeping the wiring here lets the
two entrypoints stay decoupled.
"""
from __future__ import annotations

import logging

import redis as redis_lib

from .adapters import (
    FileSourceReader,
    MinioSourceReader,
    MineruClient,
    S3SourceReader,
    SourceReaderRegistry,
)
from .adapters.db import create_engine_from_dsn, init_db, make_session_factory
from .adapters.stub_extractor import StubExtractor
from .adapters.task_store import RedisPostgresTaskStore
from .config import Settings
from .domain.parser import DocumentParser
from .ports.document_extractor import DocumentExtractor
from .ports.task_store import TaskStore

logger = logging.getLogger("py_parse.factory")


def build_registry(settings: Settings) -> SourceReaderRegistry:
    """Construct the scheme->reader routing table from settings."""
    registry = SourceReaderRegistry()
    registry.register("file", FileSourceReader())

    if settings.minio_endpoint:
        import minio

        client = minio.Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        registry.register("minio", MinioSourceReader(client))
        logger.info("registered storage backend: minio (%s)", settings.minio_endpoint)

    if settings.s3_endpoint_url or settings.s3_access_key:
        import boto3

        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )
        registry.register("s3", S3SourceReader(client))
        logger.info("registered storage backend: s3")

    return registry


def build_extractor(settings: Settings) -> DocumentExtractor:
    if settings.extractor_backend == "stub":
        return StubExtractor()
    return MineruClient(base_url=settings.mineru_url, timeout=settings.mineru_timeout)


def build_parser(settings: Settings) -> DocumentParser:
    return DocumentParser(build_extractor(settings))


def build_store(settings: Settings) -> TaskStore:
    """Construct the task store (Redis hot cache + PostgreSQL durable store)."""
    redis_client = redis_lib.Redis.from_url(settings.redis_url, decode_responses=True)
    engine = create_engine_from_dsn(settings.postgres_dsn)
    init_db(engine)
    session_factory = make_session_factory(engine)
    return RedisPostgresTaskStore(redis_client, session_factory, settings.result_ttl_seconds)
