"""Storage abstraction port.

The core parsing logic depends only on the SourceReader interface and has no
knowledge of where a document actually lives: local disk, local MinIO, remote
S3, or remote OSS.

Adding a new backend = write one adapter + register one scheme. The interface
and the parsing logic never change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import BinaryIO


class SourceReadError(Exception):
    """Failed to read a document source."""


class UnsupportedSchemeError(SourceReadError):
    """No reader is registered for the URI scheme."""

    def __init__(self, scheme: str) -> None:
        super().__init__(f"no SourceReader registered for scheme: {scheme!r}")
        self.scheme = scheme


class SourceReader(ABC):
    """Read a document referenced by a uniform URI, returning a binary stream.

    The scheme selects the storage backend; the path part is the location
    within that backend. Connection config (endpoints, credentials) is NOT
    in the URI — it is injected at adapter construction time.
    """

    @abstractmethod
    def read(self, source: str) -> BinaryIO:
        """Read the document pointed to by `source`, returning a readable binary stream.

        The caller is responsible for closing the returned stream once done.
        Raises SourceReadError on failure.
        """
        raise NotImplementedError
