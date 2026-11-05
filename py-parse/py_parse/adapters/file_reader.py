"""Local filesystem adapter: file:///data/docs/doc_001.pdf"""
from __future__ import annotations

from typing import BinaryIO
from urllib.parse import urlparse
from urllib.request import url2pathname

from ..ports.source_reader import SourceReader, SourceReadError

_SCHEME = "file"


class FileSourceReader(SourceReader):
    """Read a local file. No connection config needed."""

    def read(self, source: str) -> BinaryIO:
        p = urlparse(source)
        if p.scheme != _SCHEME:
            raise SourceReadError(
                f"FileSourceReader got scheme {p.scheme!r}, want {_SCHEME!r}"
            )
        if p.netloc not in ("", "localhost"):
            raise SourceReadError(f"unsupported host in file URI: {p.netloc!r}")

        # url2pathname handles platform differences (Linux paths vs Windows drive letters).
        local_path = url2pathname(p.path)
        try:
            return open(local_path, "rb")
        except OSError as e:
            raise SourceReadError(f"failed to open {local_path!r}: {e}") from e
