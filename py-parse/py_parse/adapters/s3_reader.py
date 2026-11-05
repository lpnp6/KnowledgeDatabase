"""Remote S3-compatible storage adapter: s3://bucket/path/to/doc.pdf

region / credentials are injected at construction time, never in the source URI.
"""
from __future__ import annotations

from typing import BinaryIO
from urllib.parse import urlparse

from ..ports.source_reader import SourceReader, SourceReadError

_SCHEME = "s3"


class S3SourceReader(SourceReader):
    def __init__(self, client) -> None:
        # `client` is a boto3 S3 client, created by the caller from env config.
        self._client = client

    def read(self, source: str) -> BinaryIO:
        p = urlparse(source)
        if p.scheme != _SCHEME:
            raise SourceReadError(
                f"S3SourceReader got scheme {p.scheme!r}, want {_SCHEME!r}"
            )
        bucket = p.netloc
        key = p.path.lstrip("/")
        if not bucket or not key:
            raise SourceReadError(f"invalid s3 source: {source!r}")

        try:
            resp = self._client.get_object(Bucket=bucket, Key=key)
            # StreamingBody: supports read()/close().
            return resp["Body"]  # type: ignore[return-value]
        except Exception as e:  # noqa: BLE001 - normalize into the port's error type
            raise SourceReadError(f"s3 get_object failed: {e}") from e
