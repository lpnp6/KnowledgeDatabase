#!/usr/bin/env python
"""gRPC client for `parse.v1.ParseService` (async submit/poll).

Submit a document by `source` URI, poll `GetResult` until done, and stream the
chunks to stdout as JSONL.

    python scripts/grpc_client.py file:///path/to/doc.pdf \
        --doc-id sample \
        --addr localhost:50051 \
        --output-dir out
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import grpc  # noqa: E402

from py_parse.generated import (  # noqa: E402
    GetResultRequest,
    ParseRequest,
    ParseServiceStub,
    TaskStatus,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="document URI: file:///... | minio://... | s3://...")
    ap.add_argument("--doc-id", default="sample")
    ap.add_argument("--addr", default="localhost:50051")
    ap.add_argument("--poll-interval", type=float, default=0.5, help="seconds between polls")
    ap.add_argument("--timeout", type=float, default=600.0, help="seconds before giving up")
    ap.add_argument("--output-dir", default=None, help="persist chunks as JSONL")
    args = ap.parse_args()

    channel = grpc.insecure_channel(args.addr)
    stub = ParseServiceStub(channel)

    # 1. Submit
    submit = stub.SubmitParse(ParseRequest(doc_id=args.doc_id, source=args.source))
    task_id = submit.task_id
    print(f"# submitted task_id={task_id}", file=sys.stderr)

    # 2. Poll until terminal state or timeout
    deadline = time.monotonic() + args.timeout
    last_status = None
    while True:
        resp = stub.GetResult(GetResultRequest(task_id=task_id))
        if resp.status != last_status:
            print(f"# status={TaskStatus.Name(resp.status)}", file=sys.stderr)
            last_status = resp.status

        if resp.status == TaskStatus.TASK_STATUS_SUCCESS:
            _emit_chunks(resp, args)
            return
        if resp.status == TaskStatus.TASK_STATUS_FAILED:
            print(f"# FAILED: {resp.error}", file=sys.stderr)
            sys.exit(1)

        if time.monotonic() >= deadline:
            print("# timeout waiting for task", file=sys.stderr)
            sys.exit(1)
        time.sleep(args.poll_interval)


def _emit_chunks(resp, args) -> None:
    out_path = None
    if args.output_dir:
        out_dir = Path(args.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{args.doc_id}.jsonl"

    fh = out_path.open("w", encoding="utf-8") if out_path else None
    try:
        for c in resp.chunks:
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

        if resp.HasField("result"):
            r = resp.result
            print(
                f"# done: chunks={r.chunk_count} pages={r.doc_meta.total_pages} "
                f"title={r.doc_meta.title!r}",
                file=sys.stderr,
            )
    finally:
        if fh:
            fh.close()


if __name__ == "__main__":
    main()
