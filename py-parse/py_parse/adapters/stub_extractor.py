"""Deterministic `DocumentExtractor` for tests / local dev (no MinerU needed).

Returns a fixed `content_list` so the whole read→extract→chunk pipeline runs
without a live MinerU service. Selected via `EXTRACTOR_BACKEND=stub`.
"""
from __future__ import annotations

from ..ports.document_extractor import DocumentExtractor

_CONTENT_LIST: list[dict] = [
    {"type": "doc_title", "content": "A Sample Document", "page_idx": 0},
    {
        "type": "text",
        "text": (
            "This is the first paragraph of body text. It is long enough to form "
            "a meaningful chunk and exercises the normal heading-grouping path."
        ),
        "text_level": 0,
        "page_idx": 0,
    },
    {
        "type": "text",
        "text": "A second paragraph that follows the first under the same heading.",
        "text_level": 0,
        "page_idx": 0,
    },
    {
        "type": "table",
        "table_caption": "Example table",
        "table_body": "<table><tr><td>a</td><td>b</td></tr><tr><td>1</td><td>2</td></tr></table>",
        "page_idx": 1,
    },
    {"type": "image", "image_caption": "Figure 1: an illustration", "page_idx": 1},
]


class StubExtractor(DocumentExtractor):
    """Returns a fixed content_list, ignoring the input bytes."""

    def extract(self, pdf_bytes: bytes) -> list[dict]:
        return list(_CONTENT_LIST)
