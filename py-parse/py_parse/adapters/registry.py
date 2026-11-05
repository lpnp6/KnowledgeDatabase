"""scheme → SourceReader routing table (pluggable).

Adding a new backend = write an adapter and register a scheme. Upstream
parsing logic never changes.
"""
from __future__ import annotations

from urllib.parse import urlparse

from ..ports.source_reader import SourceReader, UnsupportedSchemeError


class SourceReaderRegistry:
    def __init__(self) -> None:
        self._readers: dict[str, SourceReader] = {}

    def register(self, scheme: str, reader: SourceReader) -> None:
        """Register a reader for a URI scheme."""
        self._readers[scheme] = reader

    def get(self, scheme: str) -> SourceReader:
        """Return the reader for a scheme, or raise UnsupportedSchemeError."""
        reader = self._readers.get(scheme)
        if reader is None:
            raise UnsupportedSchemeError(scheme)
        return reader

    def resolve(self, source: str) -> SourceReader:
        """Resolve a source URI to its reader by scheme."""
        scheme = urlparse(source).scheme
        return self.get(scheme)
