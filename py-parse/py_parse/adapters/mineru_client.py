"""MinerU adapter: parse documents via a locally deployed `mineru-api` service.

MinerU runs as a standalone HTTP service (model weights loaded once, GPU/CPU
resident). This client uploads the raw PDF to that service and returns the
flat `content_list` (blocks already in reading order) that the chunker consumes.

Deployment note (local): start the service with
    mineru-api --host 0.0.0.0 --port 8000
then point `base_url` at it (default ``http://localhost:8000``).
"""
from __future__ import annotations

from typing import Any

import httpx

from ..ports.document_extractor import DocumentExtractError, DocumentExtractor

# mineru-api's synchronous parse endpoint (FastAPI). Multipart file upload with
# `return_content_list=true` returns the structured block list instead of just
# Markdown. MinerU 4.1+ moved this under `/v1` (breaking change); override with
# `parse_path` for other versions (e.g. older `/file_parse` or `/api/parse`).
_PARSE_PATH = "/v1/file_parse"
_HEALTH_PATH = "/health"


class MineruClient(DocumentExtractor):
    """Thin HTTP client over a local mineru-api deployment."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: float = 300.0,
        parse_path: str = _PARSE_PATH,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._parse_path = parse_path

    def extract(self, pdf_bytes: bytes) -> list[dict]:
        url = f"{self._base_url}{self._parse_path}"
        files = {"files": ("doc.pdf", pdf_bytes, "application/pdf")}
        data = {"return_content_list": "true"}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.post(url, files=files, data=data)
                resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DocumentExtractError(f"mineru-api request failed: {e}") from e

        return self._parse_content_list(resp.json())

    def health(self) -> bool:
        """Best-effort liveness probe; returns False instead of raising."""
        try:
            with httpx.Client(timeout=10.0) as client:
                return client.get(f"{self._base_url}{_HEALTH_PATH}").is_success
        except httpx.HTTPError:
            return False

    @staticmethod
    def _parse_content_list(payload: Any) -> list[dict]:
        """Tolerate the couple of response shapes mineru-api has emitted."""
        # Shape 1: the response IS the content list.
        if isinstance(payload, list):
            return payload
        # Shape 2: an object wrapping the list under a `content_list` key.
        if isinstance(payload, dict) and "content_list" in payload:
            value = payload["content_list"]
            if isinstance(value, list):
                return value
        raise DocumentExtractError(
            "unexpected mineru-api response: expected a content list "
            f"(array or object with `content_list`), got {type(payload).__name__}"
        )
