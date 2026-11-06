"""Document parser orchestrator: read → extract → chunk.

Reads the raw document bytes, delegates structured extraction to a
`DocumentExtractor` (e.g. a local MinerU service), then chunks the resulting
`content_list`. Reading order and heading detection are owned by the extractor,
not re-derived here — this is what removes the old bbox-sort / figure-title
misattribution problems.
"""
from __future__ import annotations

from typing import BinaryIO

from ..ports.document_extractor import DocumentExtractor
from .chunker import chunk_content_list
from .models import Chunk, DocMeta, ParseOutput


class DocumentParser:
    def __init__(self, extractor: DocumentExtractor) -> None:
        self._extractor = extractor

    def parse(self, pdf_stream: BinaryIO, doc_id: str) -> ParseOutput:
        pdf_bytes = pdf_stream.read()

        content_list = self._extractor.extract(pdf_bytes)

        chunks: list[Chunk] = chunk_content_list(content_list, doc_id)

        total_pages = max(
            (int(b.get("page_idx", 0)) for b in content_list),
            default=0,
        ) + 1

        return ParseOutput(
            doc_meta=DocMeta(total_pages=total_pages, title=self._extract_title(content_list)),
            chunks=chunks,
        )

    @staticmethod
    def _extract_title(content_list: list[dict]) -> str:
        """Best-effort document title = the first level-1 heading."""
        for block in content_list:
            if block.get("type") == "text" and block.get("text_level") == 1:
                text = block.get("text") or ""
                if text.strip():
                    return text.strip()
        return ""
