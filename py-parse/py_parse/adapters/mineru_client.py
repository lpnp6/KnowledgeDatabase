"""MinerU adapter: parse documents via a locally deployed MinerU HTTP service.

MinerU 4.1+ serves synchronous single-file parsing from the standalone
`mineru-kit file-parse-server` wrapper (`POST /v1/file_parse`), not from the
main `mineru-kit api-server` (which only exposes the asynchronous Tasks API).
The wrapper accepts **only** multipart/form-data:

    file            the document, one part
    output_formats  a form field (repeatable), never a query parameter;
                    `default` returns a ZIP with markdown, middle JSON and
                    structured_content.json

This client posts `file` + `output_formats=default`, unpacks
`structured_content.json` from the ZIP, and flattens its
`pages[].blocks[]` into one reading-ordered list of block dicts (each
augmented with `page_idx`) for the chunker to consume.

Two MinerU quirks are absorbed here so the domain layer never sees them:

- figure/table captions arrive as `[{"bbox": ..., "content": ...}]`, not as
  the flat strings the port documents — normalised to `captions: list[str]`;
- `structured_content` renders tables to markdown *after* dropping
  rowspan/colspan, which mangles multi-level headers. `middle_json.json` in
  the same ZIP still has the original HTML table body, so it is attached back
  as `table_body` (matched by page + bbox) for the chunker to re-render.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import httpx

from ..ports.document_extractor import DocumentExtractError, DocumentExtractor

_PARSE_PATH = "/v1/file_parse"
# The wrapper has no dedicated health route; /docs is served by its own app.
_HEALTH_PATH = "/docs"
_STRUCTURED_CONTENT_MEMBER = "structured_content.json"
_MIDDLE_JSON_MEMBER = "middle_json.json"
_OUTPUT_FORMATS = "default"
# Caption fields MinerU has used across versions; first non-empty one wins.
_CAPTION_KEYS = ("captions", "caption", "image_caption", "table_caption")


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
        # file-parse-server rejects query params and unknown form fields, so
        # everything travels as multipart parts.
        data = {"output_formats": _OUTPUT_FORMATS}
        files = {"file": ("doc.pdf", pdf_bytes, "application/pdf")}
        try:
            resp = self._client.post(url, data=data, files=files)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise DocumentExtractError(f"mineru-api request failed: {e}") from e

        structured_content, middle_json = self._bundle(resp)
        blocks = self._flatten(structured_content)
        self._normalize_captions(blocks)
        self._attach_table_html(blocks, middle_json)
        return blocks

    def health(self) -> bool:
        """Best-effort liveness probe; returns False instead of raising."""
        try:
            return self._client.get(f"{self._base_url}{_HEALTH_PATH}").is_success
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        """Release the underlying HTTP client (call when no longer needed)."""
        self._client.close()

    @classmethod
    def _bundle(cls, resp: httpx.Response) -> tuple[dict, dict | None]:
        """Pull (structured_content, middle_json) out of the ZIP attachment.

        `middle_json` is optional: it is only used to recover table HTML, so a
        missing member degrades to the markdown fallback instead of failing.
        """
        content_type = resp.headers.get("content-type", "")
        blob = resp.content
        if "zip" in content_type or blob[:2] == b"PK":
            try:
                with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                    names = archive.namelist()
                    structured = cls._member(archive, names, _STRUCTURED_CONTENT_MEMBER)
                    if structured is None:
                        raise DocumentExtractError(
                            f"mineru-api ZIP has no {_STRUCTURED_CONTENT_MEMBER}: {names}"
                        )
                    middle_name = cls._find_member(names, _MIDDLE_JSON_MEMBER)
                    middle = (
                        json.loads(archive.read(middle_name)) if middle_name else None
                    )
                    return json.loads(structured), middle
            except zipfile.BadZipFile as e:
                raise DocumentExtractError("mineru-api returned a corrupt ZIP") from e

        # 202/wait-expiry and error bodies are JSON status responses.
        try:
            payload = resp.json()
        except ValueError as e:
            raise DocumentExtractError(
                f"unexpected mineru-api response content-type {content_type!r}"
            ) from e
        status = payload.get("status") if isinstance(payload, dict) else None
        raise DocumentExtractError(
            f"mineru-api returned status {status!r}, expected an attachment"
        )

    @staticmethod
    def _find_member(names: list[str], basename: str) -> str | None:
        for name in names:
            if name.rsplit("/", 1)[-1] == basename:
                return name
        return None

    @classmethod
    def _member(cls, archive: zipfile.ZipFile, names: list[str], basename: str):
        found = cls._find_member(names, basename)
        return archive.read(found) if found else None

    @classmethod
    def _normalize_captions(cls, blocks: list[dict]) -> None:
        """Flatten `captions: [{"bbox": .., "content": ..}]` to `list[str]`."""
        for block in blocks:
            for key in _CAPTION_KEYS:
                raw = block.get(key)
                if not raw:
                    continue
                items = raw if isinstance(raw, list) else [raw]
                texts: list[str] = []
                for item in items:
                    if isinstance(item, dict):
                        item = item.get("content") or item.get("text") or ""
                    item = str(item).strip()
                    if item:
                        texts.append(item)
                if texts:
                    block["captions"] = texts
                    break

    @classmethod
    def _attach_table_html(cls, blocks: list[dict], middle: dict | None) -> None:
        """Attach `middle_json`'s rowspan/colspan-preserving table HTML."""
        if not isinstance(middle, dict):
            return
        html_by_key: dict[tuple, str] = {}
        for page in middle.get("pages") or []:
            page_idx = int(page.get("page_idx", 0))
            for block in page.get("blocks") or []:
                if block.get("type") != "table":
                    continue
                key = cls._bbox_key(page_idx, block.get("bbox"))
                body = next(
                    (
                        part for part in block.get("content") or []
                        if isinstance(part, dict) and part.get("type") == "table_body"
                    ),
                    None,
                )
                if key is not None and body and body.get("content"):
                    html_by_key[key] = body["content"]
        if not html_by_key:
            return
        for block in blocks:
            if block.get("type") != "table":
                continue
            key = cls._bbox_key(int(block.get("page_idx", 0)), block.get("bbox"))
            html = html_by_key.get(key)
            if html:
                block["table_body"] = html

    @staticmethod
    def _bbox_key(page_idx: int, bbox) -> tuple | None:
        try:
            return (page_idx, tuple(round(float(v), 4) for v in bbox))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _flatten(payload: Any) -> list[dict]:
        """Flatten `structured_content.json` `pages[].blocks[]` into block dicts."""
        if not isinstance(payload, dict):
            raise DocumentExtractError(
                f"unexpected structured_content: expected object, got {type(payload).__name__}"
            )
        try:
            pages = payload["pages"]
        except (KeyError, TypeError) as e:
            raise DocumentExtractError(
                "unexpected structured_content shape: missing pages"
            ) from e

        blocks: list[dict] = []
        for page in pages:
            page_idx = int(page.get("page_idx", 0))
            for block in page.get("blocks", []):
                flat = dict(block)
                flat["page_idx"] = page_idx
                blocks.append(flat)
        return blocks
