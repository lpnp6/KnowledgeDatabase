#!/usr/bin/env python
"""gRPC client for `parse.v1.ParseService`.

Parse a document by `source` URI and stream the chunks back to stdout as JSONL.

    python scripts/grpc_client.py file:///path/to/doc.pdf \
        --doc-id sample \
        --addr localhost:50051 \
        --output-dir out
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import grpc  # noqa: E402

from py_parse.generated import ParseRequest, ParseServiceStub  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="document URI: file:///... | minio://... | s3://...")
    ap.add_argument("--doc-id", default="sample")
    ap.add_argument("--addr", default="localhost:50051")
    ap.add_argument("--output-dir", default=None, help="persist chunks as JSONL")
    args = ap.parse_args()

    channel = grpc.insecure_channel(args.addr)
    stub = ParseServiceStub(channel)

    out_path = None
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{args.doc_id}.jsonl"

    fh = out_path.open("w", encoding="utf-8") if out_path else None
    try:
        for resp in stub.Parse(ParseRequest(doc_id=args.doc_id, source=args.source)):
            if resp.HasField("chunk"):
                c = resp.chunk
                line = json.dumps(
                    {
                        "chunk_id": c.chunk_id,
                        "text": c.text,
                        "page": c.metadata.page,
                        "heading": c.metadata.heading,
                        "chunk_type": c.metadata.chunk_type,
                    },
                    ensure_ascii=False,
                )
                print(line)
                if fh:
                    fh.write(line + "\n")
            elif resp.HasField("done"):
                done = resp.done
                print(
                    f"# done: chunks={done.chunk_count} "
                    f"pages={done.doc_meta.total_pages} title={done.doc_meta.title!r}",
                    file=sys.stderr,
                )
                if done.doc_meta.warning:
                    print(f"# warning: {done.doc_meta.warning}", file=sys.stderr)
    except grpc.RpcError as e:
        print(f"gRPC error: {e.code().name}: {e.details()}", file=sys.stderr)
        sys.exit(1)
    finally:
        if fh:
            fh.close()


if __name__ == "__main__":
    main()
