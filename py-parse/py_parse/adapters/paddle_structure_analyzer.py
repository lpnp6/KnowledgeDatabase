"""PP-Structure adapter: ML layout detection for scanned documents.

This is where the ML "detection" happens — classifying each block on a page as
title / text / table / figure. The core chunking logic only consumes the
canonical PageElement vocabulary, so this engine is swappable.

Targets PaddleOCR 3.x (PPStructureV3): `predict(input)` returns a list of
result dicts, each carrying a `parsing_res_list` of blocks with keys
`block_label`, `block_content`, `block_bbox`.
"""
from __future__ import annotations

from ..ports.layout_analyzer import LayoutAnalyzer, PageElement

# PP-StructureV3 block labels (lowercased) → our canonical vocabulary.
_TYPE_MAP = {
    "title": "title",
    "text": "text",
    "table": "table",
    "figure": "figure",
    "image": "figure",
    "chart": "figure",
    "formula": "other",
    "equation": "other",
    "seal": "other",
    "header": "header",
    "footer": "footer",
    "reference": "text",
    "abstract": "text",
    "content": "text",
    "table_title": "title",
    "figure_title": "title",
    "table_caption": "text",
    "figure_caption": "text",
}


class PaddleStructureAnalyzer(LayoutAnalyzer):
    def __init__(self, engine=None, enable_mkldnn=False, **engine_kwargs):
        # Lazily import so the package imports even when paddleocr is absent.
        if engine is None:
            from paddleocr import PPStructureV3

            # enable_mkldnn=False avoids a known paddlepaddle 3.3.x CPU oneDNN
            # regression (PIR attribute conversion failure). Safe to re-enable
            # on GPU or once the upstream bug is fixed.
            engine = PPStructureV3(enable_mkldnn=enable_mkldnn, **engine_kwargs)
        self._engine = engine

    def analyze(self, page_image: bytes, page_index: int) -> list[PageElement]:
        import cv2
        import numpy as np

        img = cv2.imdecode(np.frombuffer(page_image, np.uint8), cv2.IMREAD_COLOR)
        # `predict` returns a list of result dicts (one per input image).
        result = self._engine.predict(img)

        elements: list[PageElement] = []
        for single_img_res in result:
            for block in single_img_res.get("parsing_res_list", []):
                elements.append(
                    PageElement(
                        page=page_index,
                        element_type=self._map_type(getattr(block, "label", "")),
                        text=getattr(block, "content", "") or "",
                        bbox=self._extract_bbox(getattr(block, "bbox", None)),
                    )
                )
        return elements

    @staticmethod
    def _map_type(label: str) -> str:
        return _TYPE_MAP.get(label.lower(), "text")

    @staticmethod
    def _extract_bbox(bbox) -> tuple[float, float, float, float]:
        if not bbox or len(bbox) < 4:
            return (0.0, 0.0, 0.0, 0.0)
        return tuple(float(v) for v in bbox[:4])
