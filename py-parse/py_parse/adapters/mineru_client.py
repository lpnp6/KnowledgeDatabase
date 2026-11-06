"""MinerU adapter: parse documents via a locally deployed MinerU HTTP service.

MinerU 4.1+ exposes a V1 API (`POST /v1/file_parse`). The PDF goes in as a
multipart *file part* (`files`); options go as **query-string** params — the
endpoint rejects any multipart field (`Too many fields`), so no form fields are
sent. `output_formats=structured_content` returns pages→blocks inline; this
client flattens that into a single reading-ordered list of block dicts (each
augmented with `page_idx`) for the chunker to consume.
"""
from __future__ import annotations

from typing import Any

import httpx

from ..ports.document_extractor import DocumentExtractError, DocumentExtractor

_PARSE_PATH = "/v1/file_parse"
_HEALTH_PATH = "/health"


class MineruClient(DocumentExtractor):
    """Thin HTTP client over a local mineru-api deployment."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: float = 3600.0,
        parse_path: str = _PARSE_PATH,
        max_connections: int = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._parse_path = parse_path
        # One shared Client: thread-safe, reuses the connection pool across
        # concurrent requests. `max_connections` should be >= the worker's
        # concurrency so each in-flight request gets its own pooled connection.
        self._client = httpx.Client(
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_connections,
            ),
        )

    def extract(self, pdf_bytes: bytes) -> list[dict]:
        url = f"{self._base_url}{self._parse_path}"
        params = {"output_formats": "structured_content"}
        files = {"files": ("doc.pdf", pdf_bytes, "application/pdf")}
        try:
            resp = self._client.post(url, params=params, files=files)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DocumentExtractError(f"mineru-api request failed: {e}") from e

        return self._flatten(resp.json())

    def health(self) -> bool:
        """Best-effort liveness probe; returns False instead of raising."""
        try:
            return self._client.get(f"{self._base_url}{_HEALTH_PATH}").is_success
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        """Release the underlying HTTP client (call when no longer needed)."""
        self._client.close()

    @staticmethod
    def _flatten(payload: Any) -> list[dict]:
        """Flatten `files[0].content.structured_content.pages[]` into block dicts."""
        if not isinstance(payload, dict):
            raise DocumentExtractError(
                f"unexpected mineru-api response: expected object, got {type(payload).__name__}"
            )
        status = payload.get("status")
        if status not in (None, "completed"):
            raise DocumentExtractError(f"mineru-api returned status {status!r}")

        try:
            pages = payload["files"][0]["content"]["structured_content"]["pages"]
        except (KeyError, IndexError, TypeError) as e:
            raise DocumentExtractError(
                "unexpected response shape: missing files[0].content.structured_content.pages"
            ) from e

        blocks: list[dict] = []
        for page in pages:
            page_idx = int(page.get("page_idx", 0))
            for block in page.get("blocks", []):
                flat = dict(block)
                flat["page_idx"] = page_idx
                blocks.append(flat)
        return blocks
