"""MinIO adapter: minio://bucket/path/to/doc.pdf

Works for local or remote MinIO. endpoint / access_key / secret_key are
injected at construction time, never in the source URI.
"""
from __future__ import annotations

from typing import BinaryIO
from urllib.parse import urlparse

from ..ports.source_reader import SourceReader, SourceReadError

_SCHEME = "minio"


class MinioSourceReader(SourceReader):
    def __init__(self, client) -> None:
        # `client` is a minio.Minio instance, created by the caller from env config.
        self._client = client

    def read(self, source: str) -> BinaryIO:
        p = urlparse(source)
        if p.scheme != _SCHEME:
            raise SourceReadError(
                f"MinioSourceReader got scheme {p.scheme!r}, want {_SCHEME!r}"
            )
        bucket = p.netloc
        key = p.path.lstrip("/")
        if not bucket or not key:
            raise SourceReadError(f"invalid minio source: {source!r}")

        try:
            # get_object returns a stream-like object supporting read()/close().
            return self._client.get_object(bucket, key)  # type: ignore[return-value]
        except Exception as e:  # noqa: BLE001 - normalize into the port's error type
            raise SourceReadError(f"minio get_object failed: {e}") from e
