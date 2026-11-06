"""Chunking over MinerU's `content_list`.

MinerU already returns blocks in correct reading order, each carrying a `type`
(text / image / table / equation / code / list), a `text_level` for headings
(0/null = body, >0 = heading level), and a `page_idx`. This module groups body
text under the nearest heading and packs it into target-size chunks; tables /
code / captioned images become their own chunks.

Rules:
  - primary unit: semantic paragraph;
  - target size: 200–500 tokens;
  - minimum: ~100 tokens — fragments below this are merged upward;
  - overlap: 10–15% between adjacent text chunks, aligned to sentence boundaries;
  - the nearest heading is prepended to each chunk's text.
"""
from __future__ import annotations

import html as _html
import re

from .models import Chunk, ChunkMetadata

# Token budget (adaptive per language — see estimate_tokens).
MIN_TOKENS = 100  # ~210 CJK chars / ~400 latin chars; below this, merge
MAX_TOKENS = 500
OVERLAP_RATIO = 0.12  # 10–15%

_CJK_CHARS_PER_TOKEN = 2.1
_LATIN_CHARS_PER_TOKEN = 4.0

# Sentence enders: CJK + ASCII punctuation, plus the English full stop and
# newline (previously missing — the source of mid-word splits on English text).
_SENTENCE_END = re.compile(r"[。！？!?；;.\n]")

_CJK = re.compile(r"[一-鿿]")


def estimate_tokens(text: str) -> int:
    """Token estimate adapted to the dominant script (CJK ~2.1 chars/token,
    latin ~4 chars/token)."""
    if not text:
        return 0
    cjk_count = len(_CJK.findall(text))
    ratio = cjk_count / len(text)
    chars_per_token = _CJK_CHARS_PER_TOKEN if ratio > 0.3 else _LATIN_CHARS_PER_TOKEN
    return max(1, int(len(text) / chars_per_token))


def split_sentences(text: str) -> list[str]:
    """Split into sentences, keeping ending punctuation."""
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


def _overlap_tail(prev_text: str, ratio: float) -> str:
    """Return a trailing slice of `prev_text` (~ratio of its length), cut at a
    sentence boundary so overlap never starts mid-word."""
    if not prev_text:
        return ""
    target = int(len(prev_text) * ratio)
    sentences = split_sentences(prev_text)
    tail: list[str] = []
    acc = 0
    for s in reversed(sentences):
        if acc + len(s) > target and acc > 0:
            break
        tail.append(s)
        acc += len(s)
    return "".join(reversed(tail))


def assemble_content(paragraphs: list[str]) -> list[str]:
    """Pack paragraphs into target-size content strings, with sentence-aligned
    overlap between adjacent chunks."""
    # 1. Split oversized paragraphs.
    pieces: list[str] = []
    for para in paragraphs:
        if estimate_tokens(para) > MAX_TOKENS:
            pieces.extend(split_long_paragraph(para))
        else:
            pieces.append(para)

    # 2. Pack into target-size chunks.
    packed: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for piece in pieces:
        pt = estimate_tokens(piece)
        if current and current_tokens + pt > MAX_TOKENS:
            packed.append("".join(current))
            current = []
            current_tokens = 0
        current.append(piece)
        current_tokens += pt
    if current:
        packed.append("".join(current))

    # 3. Merge undersized chunks upward (avoid tiny fragments).
    merged: list[str] = []
    for c in packed:
        if merged and estimate_tokens(c) < MIN_TOKENS:
            merged[-1] += c
        else:
            merged.append(c)

    # 4. Apply sentence-aligned overlap: prepend tail of previous chunk.
    result: list[str] = []
    for i, c in enumerate(merged):
        if i == 0:
            result.append(c)
        else:
            result.append(_overlap_tail(merged[i - 1], OVERLAP_RATIO) + c)
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


def _join_text(value) -> str:
    """Coerce a scalar or list-of-str field (e.g. captions) into one string."""
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(str(v) for v in value if v)
    return str(value)


def _html_to_text(fragment: str) -> str:
    """Lightweight HTML→plain-text for `table_body`, keeping cell/row structure."""
    if not fragment:
        return ""
    fragment = re.sub(r"(?is)<(?:td|th)[^>]*>", " | ", fragment)
    fragment = re.sub(r"(?is)<(?:tr|/tr)[^>]*>", "\n", fragment)
    fragment = re.sub(r"(?is)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?is)<[^>]+>", "", fragment)
    fragment = _html.unescape(fragment)
    fragment = re.sub(r"[ \t]+", " ", fragment)
    fragment = re.sub(r"\n[ \t]*\n+", "\n", fragment)
    return fragment.strip()


def chunk_content_list(content_list: list[dict], doc_id: str) -> list[Chunk]:
    """Chunk a MinerU `content_list` into `Chunk` objects.

    Blocks are assumed to already be in reading order. Text with `text_level`
    > 0 sets the current heading; body text (and lists/equations) accumulate
    and get packed; tables / code / captioned images become dedicated chunks.
    """
    chunks: list[Chunk] = []
    idx = 0
    current_heading = ""
    body: list[tuple[str, int]] = []  # (text, page)

    def flush_body() -> None:
        nonlocal idx, body
        if not body:
            return
        page = body[0][1]
        paragraphs = [t for t, _ in body]
        for content in assemble_content(paragraphs):
            chunks.append(_make_chunk(current_heading, content, "text", page, doc_id, idx))
            idx += 1
        body = []

    for block in content_list:
        btype = block.get("type", "text")
        page = int(block.get("page_idx", 0))

        if btype == "text":
            level = block.get("text_level")
            text = block.get("text", "") or ""
            if level is not None and level > 0:
                flush_body()
                current_heading = text
            else:
                body.append((text, page))

        elif btype in ("list", "equation"):
            body.append((block.get("text", "") or "", page))

        elif btype == "table":
            flush_body()
            caption = _join_text(block.get("table_caption"))
            body_text = _html_to_text(block.get("table_body", "") or "")
            content = "\n".join(p for p in (caption, body_text) if p)
            if content:
                chunks.append(_make_chunk(current_heading, content, "table", page, doc_id, idx))
                idx += 1

        elif btype == "image":
            caption = _join_text(block.get("image_caption"))
            if caption:
                flush_body()
                chunks.append(_make_chunk(current_heading, caption, "image", page, doc_id, idx))
                idx += 1

        elif btype == "code":
            flush_body()
            code = block.get("text", "") or ""
            if code:
                chunks.append(_make_chunk(current_heading, code, "code", page, doc_id, idx))
                idx += 1

        # unknown types are ignored

    flush_body()
    return chunks
