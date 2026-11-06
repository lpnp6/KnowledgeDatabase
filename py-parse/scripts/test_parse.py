#!/usr/bin/env python
"""Validate extraction + chunking against a local MinerU service.

Prerequisite: a `mineru-api` service running locally (see README), e.g.
    mineru-api --host 0.0.0.0 --port 8000

Run from the py-parse/ directory:
    python scripts/test_parse.py /path/to/doc.pdf --doc-id sample --output-dir out

Single-pass report to eyeball two quality points:
  1. Extraction: does MinerU return a well-ordered content_list (types/headings)?
  2. Chunking:  are chunks 200–500 tokens with the nearest heading prepended?
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from sys import path as _sys_path

_sys_path.insert(0, str(Path(__file__).resolve().parents[1]))

from py_parse.adapters.mineru_client import MineruClient  # noqa: E402
from py_parse.domain.chunker import chunk_content_list, estimate_tokens  # noqa: E402
from py_parse.domain.models import Chunk  # noqa: E402


def read_bytes(pdf_path: str) -> bytes:
    with open(pdf_path, "rb") as f:
        return f.read()


def report_extraction(content_list: list[dict]) -> None:
    print("=" * 64)
    print("1) EXTRACTION  (what MinerU returned)")
    print("=" * 64)
    counts = Counter(b.get("type", "text") for b in content_list)
    total = sum(counts.values())
    for kind in sorted(counts):
        print(f"  {kind:10s} {counts[kind]:6d}  ({counts[kind]/total*100:.1f}%)")
    print(f"\n  blocks: {total}")

    headings = [
        b.get("content", "")
        for b in content_list
        if isinstance(b.get("type"), str) and "title" in b.get("type")
    ]
    print(f"\n  detected headings ({len(headings)}):")
    for h in headings[:25]:
        print(f"    - {h[:70]}")


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
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{doc_id}.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", help="path to a PDF document")
    ap.add_argument("--doc-id", default="sample")
    ap.add_argument("--mineru-url", default="http://localhost:8000", help="local mineru-api base URL")
    ap.add_argument("--output-dir", default=None, help="directory to persist chunks as JSONL")
    args = ap.parse_args()

    client = MineruClient(base_url=args.mineru_url)
    content_list = client.extract(read_bytes(args.pdf))
    report_extraction(content_list)

    chunks = chunk_content_list(content_list, args.doc_id)
    report_chunking(chunks)

    if args.output_dir:
        path = save_chunks(chunks, args.output_dir, args.doc_id)
        print(f"\n  chunks saved to: {path}")


if __name__ == "__main__":
    main()
