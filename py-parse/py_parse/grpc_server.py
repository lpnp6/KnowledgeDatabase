"""gRPC server exposing `parse.v1.ParseService`.

Wires the hexagonal stack into a running gRPC server:

    SourceReaderRegistry  (file:// / minio:// / s3://, from Settings)
        -> DocumentParser (MineruClient)
        -> ParseServiceServicer.Parse  (server-streaming)

The stream yields one `ParseChunk` per chunk, then a final `ParseResult`.
"""
from __future__ import annotations

import logging
from concurrent import futures

import grpc

from .adapters import FileSourceReader, MinioSourceReader, MineruClient, S3SourceReader, SourceReaderRegistry
from .config import Settings
from .domain.models import Chunk
from .domain.parser import DocumentParser
from .generated import (
    ChunkMetadata,
    DocMeta,
    ParseChunk,
    ParseRequest,
    ParseResponse,
    ParseResult,
    ParseServiceServicer,
    add_ParseServiceServicer_to_server,
)
from .ports.document_extractor import DocumentExtractError
from .ports.source_reader import SourceReadError, UnsupportedSchemeError

logger = logging.getLogger("py_parse.grpc")


def build_registry(settings: Settings) -> SourceReaderRegistry:
    """Construct the scheme->reader routing table from settings.

    `file` is always available. `minio`/`s3` are registered only when their
    connection config is present.
    """
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
    """Server-streaming implementation of `ParseService.Parse`."""

    def __init__(self, parser: DocumentParser, registry: SourceReaderRegistry) -> None:
        self._parser = parser
        self._registry = registry

    def Parse(self, request: ParseRequest, context: grpc.ServicerContext):
        doc_id = request.doc_id
        source = request.source
        logger.info("Parse start: doc_id=%r source=%r", doc_id, source)

        try:
            reader = self._registry.resolve(source)
            stream = reader.read(source)
        except UnsupportedSchemeError as e:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
            return  # unreachable; context.abort raises
        except SourceReadError as e:
            context.abort(grpc.StatusCode.NOT_FOUND, str(e))
            return

        try:
            with stream:
                output = self._parser.parse(stream, doc_id=doc_id)
        except DocumentExtractError as e:
            logger.exception("extraction failed for doc_id=%r", doc_id)
            context.abort(grpc.StatusCode.INTERNAL, str(e))
            return

        for chunk in output.chunks:
            yield ParseResponse(chunk=_to_pb_chunk(chunk))

        yield ParseResponse(
            done=ParseResult(
                chunk_count=len(output.chunks),
                doc_meta=DocMeta(
                    total_pages=output.doc_meta.total_pages,
                    title=output.doc_meta.title,
                    warning=output.doc_meta.warning,
                ),
            )
        )
        logger.info("Parse done: doc_id=%r chunks=%d", doc_id, len(output.chunks))


def build_server(settings: Settings) -> grpc.Server:
    """Create a configured-but-not-started gRPC server (health + ParseService)."""
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=settings.grpc_max_workers))

    registry = build_registry(settings)
    extractor = MineruClient(base_url=settings.mineru_url, timeout=settings.mineru_timeout)
    parser = DocumentParser(extractor)

    add_ParseServiceServicer_to_server(ParserServicer(parser, registry), server)

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
