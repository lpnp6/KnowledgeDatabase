"""Adapters: concrete implementations of the ports (replaceable).

One adapter per storage backend, routed by scheme through the registry.
Connection config is injected at construction time, never in the request.
"""

from .file_reader import FileSourceReader
from .minio_reader import MinioSourceReader
from .s3_reader import S3SourceReader
from .registry import SourceReaderRegistry

__all__ = [
    "FileSourceReader",
    "MinioSourceReader",
    "S3SourceReader",
    "SourceReaderRegistry",
]
