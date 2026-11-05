#!/usr/bin/env python
"""Validate layout detection + chunking on a real scanned document.

Run from the py-parse/ directory:
    python scripts/test_parse.py /path/to/doc.pdf --max-pages 10

Single-pass report to eyeball two quality points:
  1. Detection: does PP-Structure correctly find titles / text / tables?
  2. Chunking:  are chunks 200–500 tokens with the nearest title prepended?
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf  # noqa: E402

from py_parse.adapters.paddle_structure_analyzer import PaddleStructureAnalyzer  # noqa: E402
from py_parse.domain.chunker import chunk_group, estimate_tokens  # noqa: E402
from py_parse.domain.models import Chunk  # noqa: E402
from py_parse.ports.layout_analyzer import PageElement  # noqa: E402


def analyze(analyzer, pdf_path: str, dpi: int, max_pages: int):
    """Render + analyze pages in a single pass; return elements + timings."""
    doc = pymupdf.open(pdf_path)
    n = min(len(doc), max_pages) if max_pages else len(doc)
    elements: list[PageElement] = []
    timings: list[float] = []
    for i in range(n):
        pix = doc[i].get_pixmap(dpi=dpi)
        img = pix.tobytes("png")
        t0 = time.time()
        page_elems = analyzer.analyze(img, i)
        dt = time.time() - t0
        timings.append(dt)
        page_elems.sort(key=lambda e: (e.bbox[1], e.bbox[0]))  # top-to-bottom
        elements.extend(page_elems)
        # per-page progress (flush so it prints even while thrashing)
        print(f"  [page {i+1}/{n}] {dt:.1f}s  elements={len(page_elems)}", flush=True)
    return elements, timings, n


def group_and_chunk(elements: list[PageElement], doc_id: str) -> list[Chunk]:
    """Group content under nearest title, then chunk (mirrors the parser)."""
    chunks: list[Chunk] = []
    idx = 0
    heading = ""
    group: list[PageElement] = []

    def flush() -> None:
        nonlocal group, idx
        if group:
            made = chunk_group(heading, group, doc_id, idx)
            chunks.extend(made)
            idx += len(made)
            group = []

    for e in elements:
        if e.element_type == "title":
            flush()
            heading = e.text
        elif e.element_type in ("text", "table"):
            group.append(e)
    flush()
    return chunks


def report_detection(elements, timings, n) -> None:
    print("=" * 64)
    print("1) DETECTION  (what PP-Structure found)")
    print("=" * 64)
    counts = Counter(e.element_type for e in elements)
    total = sum(counts.values())
    for kind in sorted(counts):
        print(f"  {kind:10s} {counts[kind]:6d}  ({counts[kind]/total*100:.1f}%)")
    print(f"\n  pages: {n}   avg {sum(timings)/n:.2f} s/page")

    titles = [e.text for e in elements if e.element_type == "title"]
    print(f"\n  detected titles ({len(titles)}):")
    for t in titles[:25]:
        print(f"    - {t[:70]}")

    # Red flag: pages with no text elements (OCR failure / blank page).
    per_page = Counter(e.page for e in elements if e.element_type in ("text", "title", "table"))
    empty = [p for p in range(n) if per_page[p] == 0]
    if empty:
        print(f"\n  [WARN] pages with no text: {empty[:20]}")


def report_chunking(chunks: list[Chunk]) -> None:
    print("\n" + "=" * 64)
    print("2) CHUNKING")
    print("=" * 64)
    by_type = Counter(c.metadata.chunk_type for c in chunks)
    print(f"  total chunks: {len(chunks)}")
    for kind in sorted(by_type):
        print(f"    {kind:8s} {by_type[kind]}")

    sizes = sorted(estimate_tokens(c.text) for c in chunks if c.metadata.chunk_type == "text")
    if sizes:
        under = sum(1 for s in sizes if s < 100)
        over = sum(1 for s in sizes if s > 500)
        print(f"\n  text chunk tokens: min={sizes[0]}  median={sizes[len(sizes)//2]}  max={sizes[-1]}")
        print(f"    <100 tokens (under-merged): {under}    >500 tokens (over-split): {over}")

    print("\n  sample chunks:")
    for c in chunks[:5]:
        print(f"\n    [{c.metadata.chunk_type}] p{c.metadata.page}  heading={c.metadata.heading[:40]!r}")
        print(f"    {c.text[:160]}")


def save_chunks(chunks: list[Chunk], output_dir: str, doc_id: str) -> Path:
    """Persist chunks as JSONL to {output_dir}/{doc_id}.jsonl."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc_id}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="path to a scanned PDF")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--max-pages", type=int, default=0, help="limit pages for a quick test (0 = all)")
    ap.add_argument("--doc-id", default="sample")
    ap.add_argument("--output-dir", default=None, help="directory to persist chunks as JSONL")
    args = ap.parse_args()

    analyzer = PaddleStructureAnalyzer()
    elements, timings, n = analyze(analyzer, args.pdf, args.dpi, args.max_pages)
    report_detection(elements, timings, n)

    chunks = group_and_chunk(elements, args.doc_id)
    report_chunking(chunks)

    if args.output_dir:
        path = save_chunks(chunks, args.output_dir, args.doc_id)
        print(f"\n  chunks saved to: {path}")


if __name__ == "__main__":
    main()
