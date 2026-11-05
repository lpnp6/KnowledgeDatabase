"""Ports: the interfaces the core parsing logic depends on.

Interfaces only, no implementations. The core depends on these abstractions,
so every external concern (storage, transport) is a replaceable adapter.
"""

from .source_reader import (
    SourceReader,
    SourceReadError,
    UnsupportedSchemeError,
)

__all__ = [
    "SourceReader",
    "SourceReadError",
    "UnsupportedSchemeError",
]
