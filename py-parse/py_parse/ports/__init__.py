"""Ports: the interfaces the core parsing logic depends on.

Interfaces only, no implementations. The core depends on these abstractions,
so every external concern (storage, document extraction) is a replaceable
adapter.
"""

from .document_extractor import DocumentExtractError, DocumentExtractor
from .source_reader import (
    SourceReadError,
    SourceReader,
    UnsupportedSchemeError,
)

__all__ = [
    "DocumentExtractor",
    "DocumentExtractError",
    "SourceReader",
    "SourceReadError",
    "UnsupportedSchemeError",
]
