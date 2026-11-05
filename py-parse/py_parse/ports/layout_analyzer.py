"""Layout analysis port.

Abstracts the ML layout/OCR engine (PP-Structure) behind an interface so the
core parsing logic never depends on a specific model stack. Swapping the
engine (PP-Structure / LayoutLM / a cloud OCR) is a replaceable adapter.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class PageElement:
    """A layout element detected on one rendered page.

    `element_type` uses a small canonical vocabulary (independent of the
    engine's own labels): "title" | "text" | "table" | "figure" | "header" |
    "footer" | "other".
    """

    page: int
    element_type: str
    text: str  # OCR text; empty for pure figures
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 (pixels)
    confidence: float = 1.0


class LayoutAnalyzer(ABC):
    """Analyze one rendered page image into layout elements."""

    @abstractmethod
    def analyze(self, page_image: bytes, page_index: int) -> list[PageElement]:
        """Detect layout elements on a page image (PNG/JPEG bytes).

        Args:
            page_image: encoded page image bytes.
            page_index: 0-based page number (for attribution).

        Returns:
            A list of PageElement; caller is responsible for reading order
            (top-to-bottom) sorting across elements.
        """
        raise NotImplementedError
