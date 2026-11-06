"""Adapters: concrete implementations of the ports (replaceable).

Storage backends route by scheme through the registry; document extraction is
provided by the MinerU HTTP client. Connection config is injected at
construction time, never in the request.
"""

from .file_reader import FileSourceReader
from .mineru_client import MineruClient
from .minio_reader import MinioSourceReader
from .registry import SourceReaderRegistry
from .s3_reader import S3SourceReader

__all__ = [
    "FileSourceReader",
    "MineruClient",
    "MinioSourceReader",
    "S3SourceReader",
    "SourceReaderRegistry",
]
