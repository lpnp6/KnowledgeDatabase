"""Document extraction port.

Abstracts "turn raw PDF bytes into structured content blocks" behind an
interface. The concrete engine (MinerU via local HTTP service, a cloud API,
or an SDK) is a replaceable adapter; the core parsing logic only consumes the
canonical `content_list` shape.

`content_list` is a flat list of blocks already ordered by reading order, each
a dict carrying at least:
  - `type`:  "text" | "image" | "table" | "equation" | "code" | "list"
  - `page_idx`: 0-based page number
  and, per type, e.g. `text` + `text_level` for text, `table_body` for tables,
  `image_caption` for images.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class DocumentExtractError(Exception):
    """Failed to extract structured content from a document."""


class DocumentExtractor(ABC):
    """Extract structured content blocks from raw document bytes."""

    @abstractmethod
    def extract(self, pdf_bytes: bytes) -> list[dict]:
        """Return the flat `content_list` (reading-ordered blocks) for a document.

        Raises DocumentExtractError on failure.
        """
        raise NotImplementedError
