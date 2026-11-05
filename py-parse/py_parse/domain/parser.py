"""Document parser orchestrator: render → ML layout → group → chunk.

Renders PDF pages, runs the LayoutAnalyzer (ML) in parallel, groups content
under the nearest title (no heading hierarchy), and chunks. Page-level results
can be cached for checkpoint/resume — important for hundreds-of-pages scanned
documents.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import BinaryIO, Optional, Protocol

import pymupdf

from ..ports.layout_analyzer import LayoutAnalyzer, PageElement
from .chunker import chunk_group
from .models import Chunk, DocMeta, ParseOutput


class PageCache(Protocol):
    """Checkpoint/resume: cache analyzed pages by (doc_id, page_index)."""

    def get(self, doc_id: str, page_index: int) -> Optional[list[PageElement]]: ...

    def put(self, doc_id: str, page_index: int, elements: list[PageElement]) -> None: ...


class DocumentParser:
    def __init__(
        self,
        analyzer: LayoutAnalyzer,
        page_cache: Optional[PageCache] = None,
        dpi: int = 200,
        workers: int = 4,
    ) -> None:
        self._analyzer = analyzer
        self._page_cache = page_cache
        self._dpi = dpi
        self._workers = workers

    def parse(self, pdf_stream: BinaryIO, doc_id: str) -> ParseOutput:
        pdf_bytes = pdf_stream.read()
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)

        # 1. Analyze pages in parallel (with optional checkpoint cache).
        def analyze(i: int) -> list[PageElement]:
            if self._page_cache is not None:
                cached = self._page_cache.get(doc_id, i)
                if cached is not None:
                    return cached
            elems = self._analyze_page(doc[i], i)
            if self._page_cache is not None:
                self._page_cache.put(doc_id, i, elems)
            return elems

        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            page_elements = list(pool.map(analyze, range(total_pages)))

        # 2. Flatten in reading order (pages ordered; elements sorted within page).
        elements: list[PageElement] = [e for page in page_elements for e in page]

        # 3. Group under nearest title, then chunk.
        chunks = self._chunk_document(elements, doc_id)

        return ParseOutput(doc_meta=DocMeta(total_pages=total_pages), chunks=chunks)

    def _analyze_page(self, page, page_index: int) -> list[PageElement]:
        pix = page.get_pixmap(dpi=self._dpi)
        img_bytes = pix.tobytes("png")
        elems = self._analyzer.analyze(img_bytes, page_index)
        # Reading order: top-to-bottom, then left-to-right.
        elems.sort(key=lambda e: (e.bbox[1], e.bbox[0]))
        return elems

    def _chunk_document(self, elements: list[PageElement], doc_id: str) -> list[Chunk]:
        chunks: list[Chunk] = []
        idx = 0
        current_heading = ""
        current_elems: list[PageElement] = []

        def flush() -> None:
            nonlocal current_elems, idx
            if not current_elems:
                return
            group = chunk_group(current_heading, current_elems, doc_id, idx)
            chunks.extend(group)
            idx += len(group)
            current_elems = []

        for elem in elements:
            if elem.element_type == "title":
                flush()
                current_heading = elem.text
            elif elem.element_type in ("text", "table"):
                current_elems.append(elem)

        flush()
        return chunks
