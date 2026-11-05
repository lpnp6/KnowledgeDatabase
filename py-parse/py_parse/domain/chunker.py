"""Chunking strategy.

Rules (as agreed):
  - primary unit: semantic paragraph;
  - target size: 200–500 tokens (~420–1050 Chinese chars, ~2.1 chars/token);
  - minimum: ~100 tokens — fragments below this are merged upward, never emitted
    as tiny chunks (fixed special-token overhead would dominate);
  - overlap: 10–15% between adjacent chunks;
  - the nearest title (heading) is prepended to each chunk's text so the
    embedding captures "what section this is about".

Heading detection is done by the ML layout engine (PP-Structure); this module
only chunks content that has already been grouped under a nearest title.
"""
from __future__ import annotations

import re

from ..ports.layout_analyzer import PageElement
from .models import Chunk, ChunkMetadata

CHARS_PER_TOKEN = 2.1
MIN_TOKENS = 100  # ~210 chars; below this, merge
MAX_TOKENS = 500  # ~1050 chars; above this, split
OVERLAP_RATIO = 0.12  # 10–15%

_SENTENCE_END = re.compile(r"[。！？!?；;]")


def estimate_tokens(text: str) -> int:
    """Rough token estimate for Chinese text (~2.1 chars/token)."""
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def split_sentences(text: str) -> list[str]:
    """Split into sentence chunks, keeping ending punctuation."""
    sentences: list[str] = []
    start = 0
    for m in _SENTENCE_END.finditer(text):
        sentences.append(text[start : m.end()])
        start = m.end()
    if start < len(text):
        sentences.append(text[start:])
    return [s for s in sentences if s.strip()]


def split_long_paragraph(paragraph: str) -> list[str]:
    """Split a paragraph exceeding MAX_TOKENS at sentence boundaries."""
    pieces: list[str] = []
    current = ""
    for sentence in split_sentences(paragraph):
        if current and estimate_tokens(current + sentence) > MAX_TOKENS:
            pieces.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        pieces.append(current)
    return pieces


def assemble_content(paragraphs: list[str]) -> list[str]:
    """Assemble paragraphs into target-size content strings (no heading yet)."""
    # 1. Split oversized paragraphs.
    pieces: list[str] = []
    for para in paragraphs:
        if estimate_tokens(para) > MAX_TOKENS:
            pieces.extend(split_long_paragraph(para))
        else:
            pieces.append(para)

    # 2. Pack into target-size chunks.
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for piece in pieces:
        pt = estimate_tokens(piece)
        if current and current_tokens + pt > MAX_TOKENS:
            chunks.append("".join(current))
            current = []
            current_tokens = 0
        current.append(piece)
        current_tokens += pt
    if current:
        chunks.append("".join(current))

    # 3. Merge undersized chunks upward (avoid tiny fragments).
    merged: list[str] = []
    for c in chunks:
        if merged and estimate_tokens(c) < MIN_TOKENS:
            merged[-1] += c
        else:
            merged.append(c)

    # 4. Apply overlap: prepend tail of previous chunk.
    result: list[str] = []
    for i, c in enumerate(merged):
        if i == 0:
            result.append(c)
        else:
            overlap_len = int(len(merged[i - 1]) * OVERLAP_RATIO)
            result.append(merged[i - 1][-overlap_len:] + c)
    return result


def _make_chunk(
    heading: str, content: str, chunk_type: str, page: int, doc_id: str, idx: int
) -> Chunk:
    text = f"{heading}\n\n{content}" if heading else content
    return Chunk(
        chunk_id=f"{doc_id}#{idx}",
        text=text,
        metadata=ChunkMetadata(page=page, heading=heading, chunk_type=chunk_type),
    )


def chunk_group(
    heading: str, elements: list[PageElement], doc_id: str, start_idx: int
) -> list[Chunk]:
    """Chunk content grouped under one nearest title.

    Text elements are packed into text chunks; each table becomes its own
    chunk (overlap does not apply to tables).
    """
    page = elements[0].page if elements else 0
    idx = start_idx
    chunks: list[Chunk] = []

    paragraphs = [e.text for e in elements if e.element_type == "text"]
    for content in assemble_content(paragraphs):
        chunks.append(_make_chunk(heading, content, "text", page, doc_id, idx))
        idx += 1

    for table in elements:
        if table.element_type == "table":
            chunks.append(_make_chunk(heading, table.text, "table", table.page, doc_id, idx))
            idx += 1

    return chunks
