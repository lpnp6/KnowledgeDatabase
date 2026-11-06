"""MinerU worker: concurrent multi-lane parsing against a local mineru-api.

A worker wraps one `MineruClient` (which already shares a connection pool) with
a thread pool sized to `max_concurrency`, so up to that many parse requests run
in parallel. Tune `max_concurrency` to the GPU's VRAM (e.g. 2–4 lanes for a
12GB card) — the worker is what keeps the GPU busy by overlapping CPU/IO-bound
stages of one document with GPU-bound stages of another.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from .mineru_client import MineruClient


class MineruWorker:
    """Concurrently parse documents, bounded by `max_concurrency` lanes."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        max_concurrency: int = 2,
        timeout: float = 3600.0,
    ) -> None:
        self._max_concurrency = max_concurrency
        self._client = MineruClient(base_url=base_url, timeout=timeout)
        self._pool = ThreadPoolExecutor(max_workers=max_concurrency)

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    def submit(self, pdf_bytes: bytes) -> Future[list[dict]]:
        """Submit one document; returns a Future resolving to its content_list."""
        return self._pool.submit(self._client.extract, pdf_bytes)

    def parse_many(self, docs: list[bytes]) -> list[list[dict]]:
        """Parse many documents, up to `max_concurrency` in parallel.

        Order of results matches the input order. Raises on the first failure.
        """
        return list(self._pool.map(self._client.extract, docs))

    def close(self) -> None:
        """Shut down the pool and release the HTTP client."""
        self._pool.shutdown(wait=True)
        self._client.close()

    def __enter__(self) -> "MineruWorker":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
