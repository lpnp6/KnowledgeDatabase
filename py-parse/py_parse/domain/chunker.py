"""Chunking over MinerU's `content_list`.

MinerU already returns blocks in correct reading order, each carrying a `type`
(text / image / chart / table / equation / code / list), a `text_level` for
headings (0/null = body, >0 = heading level), and a `page_idx`. This module
groups body text under the nearest heading and packs it into target-size
chunks; tables / code / captioned images and charts become their own chunks.

Captions (`captions: list[str]`) are the only payload for figures — image
bytes live in the extractor's ZIP and are not retained here. Tables prefer
`table_body` (original HTML with rowspan/colspan) and fall back to the
extractor's flattened markdown.

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
from html.parser import HTMLParser

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

# structured_content block types that act as headings (set the nearest title).
_TITLE_TYPES = {
    "doc_title", "title", "paragraph_title", "section_title",
    "subsection_title", "chapter_title",
}
# structured_content block types to drop (page furniture, not body content).
_IGNORE_TYPES = {
    "aside_text", "page_footnote", "page_number", "header", "footer",
}


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
            tail = _overlap_tail(merged[i - 1], OVERLAP_RATIO)
            # 拼接处补空格，避免 overlap 尾部与下一 chunk 首词黏连（"leveraginglarger"）
            if tail and c and not tail[-1].isspace() and not c[0].isspace():
                result.append(tail + " " + c)
            else:
                result.append(tail + c)
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


def _caption_text(block: dict) -> str:
    """Join a block's caption(s), tolerating str / list[str] / list[dict]."""
    for key in ("captions", "caption", "image_caption", "table_caption"):
        raw = block.get(key)
        if not raw:
            continue
        items = raw if isinstance(raw, list) else [raw]
        parts: list[str] = []
        for item in items:
            if isinstance(item, dict):
                item = item.get("content") or item.get("text") or ""
            item = str(item).strip()
            if item:
                parts.append(item)
        if parts:
            return " ".join(parts)
    return ""


def _html_to_text(fragment: str) -> str:
    """Lightweight HTML→plain-text for `table_body`, keeping cell/row structure."""
    if not fragment:
        return ""
    # Keep superscript/subscript information instead of gluing digits together
    # (O(n<sup>2</sup>) → O(n^2), d<sub>k</sub> → d_k).
    fragment = re.sub(r"(?is)<sup[^>]*>(.*?)</sup>", r"^\1", fragment)
    fragment = re.sub(r"(?is)<sub[^>]*>(.*?)</sub>", r"_\1", fragment)
    fragment = re.sub(r"(?is)<(?:td|th)[^>]*>", " | ", fragment)
    fragment = re.sub(r"(?is)<(?:tr|/tr)[^>]*>", "\n", fragment)
    fragment = re.sub(r"(?is)<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?is)<[^>]+>", "", fragment)
    fragment = _html.unescape(fragment)
    fragment = re.sub(r"[ \t]+", " ", fragment)
    fragment = re.sub(r"\n[ \t]*\n+", "\n", fragment)
    return fragment.strip()


class _TableParser(HTMLParser):
    """Collect `<tr>/<td>/<th>` into rows of cells, keeping row/col spans."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[dict]] = []
        self._row: list[dict] | None = None
        self._cell: dict | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._close_row()
            self._row = []
        elif tag in ("td", "th"):
            self._close_cell()
            if self._row is None:
                self._row = []
            values = dict(attrs)
            self._cell = {
                "parts": [],
                "rowspan": self._span(values.get("rowspan")),
                "colspan": self._span(values.get("colspan")),
            }
        elif self._cell is not None:
            if tag == "br":
                self._cell["parts"].append(" ")
            elif tag == "sup":
                self._cell["parts"].append("^")
            elif tag == "sub":
                self._cell["parts"].append("_")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("td", "th"):
            self._close_cell()
        elif tag == "tr":
            self._close_row()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["parts"].append(data)

    def close(self) -> None:
        super().close()
        self._close_cell()
        self._close_row()

    @staticmethod
    def _span(value) -> int:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 1

    def _close_cell(self) -> None:
        if self._cell is None:
            return
        text = re.sub(r"\s+", " ", "".join(self._cell["parts"])).strip()
        assert self._row is not None
        self._row.append(
            {
                "text": text,
                "rowspan": self._cell["rowspan"],
                "colspan": self._cell["colspan"],
            }
        )
        self._cell = None

    def _close_row(self) -> None:
        if self._row:
            self.rows.append(self._row)
        self._row = None


def _expand_spans(rows: list[list[dict]]) -> list[list[str]]:
    """Expand rowspan/colspan into a rectangular grid, repeating spanned text.

    Repeating the value (rather than leaving blanks) is what keeps grouped rows
    readable after flattening — e.g. Table 3's "(A)" rowspan=4 labels all four
    of its rows.
    """
    grid: list[list[str]] = []
    carry: dict[int, tuple[int, str]] = {}  # col -> (rows still covered, text)
    for cells in rows:
        row: list[str] = []
        col = 0
        pending = None
        while True:
            if col in carry:
                left, text = carry[col]
                row.append(text)
                if left <= 1:
                    del carry[col]
                else:
                    carry[col] = (left - 1, text)
                col += 1
                continue
            if pending is None:
                pending = cells.pop(0) if cells else None
                if pending is None:
                    ahead = [c for c in carry if c >= col]
                    if not ahead:
                        break
                    col = min(ahead)
                    continue
            span = pending["colspan"]
            for k in range(span):
                row.append(pending["text"])
                if pending["rowspan"] > 1:
                    carry[col + k] = (pending["rowspan"] - 1, pending["text"])
            col += span
            pending = None
        grid.append(row)

    width = max((len(r) for r in grid), default=0)
    for row in grid:
        row.extend([""] * (width - len(row)))
    return grid


def _md_cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().replace("|", "\\|")


def _html_table_to_markdown(fragment: str) -> str:
    """Render an HTML table as markdown with qualified, multi-level headers.

    A two-row header (`BLEU` spanning `EN-DE`/`EN-FR`) becomes one flat header
    row whose cells read `BLEU / EN-DE`; without this the column semantics are
    lost and numbers get attributed to the wrong column.
    """
    if not fragment or "<tr" not in fragment.lower():
        return ""
    parser = _TableParser()
    parser.feed(fragment)
    parser.close()
    rows = [row for row in parser.rows if row]
    if not rows:
        return ""

    # Header rows: the first row, plus a second one when the first row carries
    # a colspan (that is the signature of a two-level header).
    header_rows = 1
    if len(rows) > 1 and any(cell["colspan"] > 1 for cell in rows[0]):
        header_rows = 2

    grid = _expand_spans(rows)
    if not grid:
        return ""
    width = len(grid[0])
    header_rows = min(header_rows, len(grid))

    headers: list[str] = []
    for col in range(width):
        parts: list[str] = []
        for r in range(header_rows):
            value = _md_cell(grid[r][col])
            if value and (not parts or parts[-1] != value):
                parts.append(value)
        headers.append(" / ".join(parts))

    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in grid[header_rows:]:
        lines.append("| " + " | ".join(_md_cell(cell) for cell in row) + " |")
    return "\n".join(lines)


_TABLE_CAPTION_LABEL = re.compile(r"Table\s+(\d+)\s*:")


def _replace_heading(chunk: Chunk, new_heading: str) -> None:
    """Rewrite the heading both in metadata and in the prepended text."""
    old = chunk.metadata.heading
    body = chunk.text[len(old) + 2:] if old else chunk.text
    chunk.text = f"{new_heading}\n\n{body}" if new_heading else body
    chunk.metadata.heading = new_heading


def _retarget_table_headings(chunks: list[Chunk]) -> None:
    """Re-home floated tables under the section that actually references them.

    Tables are laid out at the top of a page in the PDF, so sequential heading
    tracking attributes them to whatever section preceded them. The caption
    carries the table number; the nearest *body* chunk mentioning that number
    names the owning section.
    """
    for i, chunk in enumerate(chunks):
        if chunk.metadata.chunk_type != "table":
            continue
        label = _TABLE_CAPTION_LABEL.search(chunk.text)
        if not label:
            continue
        mention = re.compile(rf"\bTable\s+{label.group(1)}\b")
        best: tuple[int, str] | None = None
        for j, other in enumerate(chunks):
            if j == i or other.metadata.chunk_type != "text":
                continue
            if not mention.search(other.text):
                continue
            distance = abs(j - i)
            if best is None or distance < best[0]:
                best = (distance, other.metadata.heading)
        if best and best[1] and best[1] != chunk.metadata.heading:
            _replace_heading(chunk, best[1])


def _clean_latex(text: str) -> str:
    """Reduce LaTeX math markup to readable text (drop commands, braces, `$`)."""
    if not text:
        return ""
    text = text.replace("$", "")
    text = re.sub(r"\\[a-zA-Z]+", "", text)   # \sum, \mathrm, \mathcal, …
    text = text.replace("\\", "")             # leftover backslashes (\{, \}, etc.)
    text = re.sub(r"[{}]", "", text)          # braces
    text = re.sub(r"[ \t]+", " ", text)       # collapse runs of spaces/tabs
    return text.strip()


def _clean_inline(text: str) -> str:
    """Strip inline HTML (e.g. <sup>1</sup>) and LaTeX markup from block text."""
    if not text:
        return ""
    text = re.sub(r"(?is)<sup[^>]*>.*?</sup>", "", text)  # drop reference-number superscripts
    text = re.sub(r"(?is)<[^>]+>", "", text)
    text = _html.unescape(text)
    return _clean_latex(text)


def chunk_content_list(content_list: list[dict], doc_id: str) -> list[Chunk]:
    """Chunk a MinerU `structured_content` block list into `Chunk` objects.

    Blocks are already in reading order. A heading-type block (doc_title /
    paragraph_title / …) sets the current heading; body text (text / list /
    equation) accumulates and gets packed; tables / code / captioned images
    become dedicated chunks. Page furniture (footers, page numbers) is dropped.
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

        if btype in _TITLE_TYPES:
            flush_body()
            current_heading = _clean_inline(block.get("content", "") or "")

        elif btype == "table":
            flush_body()
            caption = _caption_text(block)
            html = block.get("table_body") or ""
            body_text = (
                _html_table_to_markdown(html)
                or _html_to_text(block.get("content") or "")
            )
            content = "\n\n".join(p for p in (caption, body_text) if p)
            if content:
                chunks.append(_make_chunk(current_heading, content, "table", page, doc_id, idx))
                idx += 1

        elif btype in ("image", "chart"):
            # Captions are the only figure payload worth embedding; charts are
            # figures too (MinerU labels plots `chart`), so they share the type.
            caption = _caption_text(block)
            if caption:
                flush_body()
                chunks.append(_make_chunk(current_heading, caption, "image", page, doc_id, idx))
                idx += 1

        elif btype == "code":
            flush_body()
            code = block.get("content", "") or ""
            if code:
                chunks.append(_make_chunk(current_heading, code, "code", page, doc_id, idx))
                idx += 1

        elif btype in _IGNORE_TYPES:
            continue  # drop footers / page numbers / aside text

        else:
            # body text: "text", "list", "equation", and anything unrecognised
            text = _clean_inline(block.get("content", "") or "")
            if text.strip():
                body.append((text, page))

    flush_body()
    _retarget_table_headings(chunks)
    return chunks
