#!/usr/bin/env python
"""Offline tests for caption handling and table re-rendering.

Run from py-parse/:
    python -m unittest tests.test_chunker -v
"""
from __future__ import annotations

import unittest
from pathlib import Path
from sys import path as _sys_path

_sys_path.insert(0, str(Path(__file__).resolve().parents[1]))

from py_parse.domain.chunker import (  # noqa: E402
    _caption_text,
    _html_table_to_markdown,
    chunk_content_list,
)

# Shape produced by MinerU for Table 2 of "Attention Is All You Need": the
# second header level lives under colspan cells.
TABLE_TWO_LEVEL = """
<table><tbody>
<tr><td rowspan="2">Model</td><td colspan="2">BLEU</td><td colspan="2">Training Cost (FLOPs)</td></tr>
<tr><td>EN-DE</td><td>EN-FR</td><td>EN-DE</td><td>EN-FR</td></tr>
<tr><td>Transformer (base model)</td><td>27.3</td><td>38.1</td><td>3.3 ·</td><td>10<sup>18</sup></td></tr>
<tr><td>Transformer (big)</td><td>28.4</td><td>41.8</td><td>2.3 ·</td><td>10<sup>19</sup></td></tr>
</tbody></table>
"""

TABLE_ROWSPAN = """
<table><tr><td>setting</td><td>value</td></tr>
<tr><td rowspan="2">(A)</td><td>1</td></tr>
<tr><td>4</td></tr></table>
"""


class HtmlTableTest(unittest.TestCase):
    def test_two_level_header_is_qualified(self):
        lines = _html_table_to_markdown(TABLE_TWO_LEVEL).splitlines()
        header = lines[0]
        self.assertIn("BLEU / EN-DE", header)
        self.assertIn("BLEU / EN-FR", header)
        self.assertIn("Training Cost (FLOPs) / EN-DE", header)
        self.assertEqual(header.count("|"), 6)  # 5 columns

    def test_two_level_header_values_stay_in_column(self):
        lines = _html_table_to_markdown(TABLE_TWO_LEVEL).splitlines()
        big = next(line for line in lines if "28.4" in line)
        cells = [c.strip() for c in big.strip("|").split("|")]
        self.assertEqual(cells[0], "Transformer (big)")
        self.assertEqual(cells[1], "28.4")   # BLEU / EN-DE
        self.assertEqual(cells[2], "41.8")   # BLEU / EN-FR

    def test_superscript_is_preserved(self):
        lines = _html_table_to_markdown(TABLE_TWO_LEVEL).splitlines()
        self.assertTrue(any("10^19" in line for line in lines))

    def test_rowspan_group_repeats(self):
        lines = _html_table_to_markdown(TABLE_ROWSPAN).splitlines()
        body = lines[2:]  # header + separator
        self.assertEqual(len(body), 2)
        self.assertIn("(A)", body[0])
        self.assertIn("(A)", body[1])

    def test_single_header_row_used_for_plain_table(self):
        md = _html_table_to_markdown(
            "<table><tr><td>k</td><td>v</td></tr><tr><td>a</td><td>b</td></tr></table>"
        )
        self.assertEqual(md.splitlines()[0], "| k | v |")

    def test_empty_fragment(self):
        self.assertEqual(_html_table_to_markdown(""), "")


class CaptionTest(unittest.TestCase):
    def test_list_of_dicts(self):
        block = {
            "captions": [{"bbox": [0, 0, 1, 1], "content": "Figure 1: The Transformer."}]
        }
        self.assertEqual(_caption_text(block), "Figure 1: The Transformer.")

    def test_plain_string_and_legacy_keys(self):
        self.assertEqual(_caption_text({"caption": " Figure 2 "}), "Figure 2")
        self.assertEqual(_caption_text({"image_caption": ["a", "b"]}), "a b")
        self.assertEqual(_caption_text({}), "")


class ChunkContentListTest(unittest.TestCase):
    def test_image_chunk_carries_caption_and_heading(self):
        blocks = [
            {"type": "paragraph_title", "page_idx": 2, "content": "3 Model Architecture"},
            {
                "type": "image",
                "page_idx": 2,
                "content": "",
                "captions": [{"content": "Figure 1: The Transformer - model architecture."}],
            },
            {"type": "text", "page_idx": 2, "content": "Body text after the figure. " * 40},
        ]
        chunks = chunk_content_list(blocks, "doc")
        image = [c for c in chunks if c.metadata.chunk_type == "image"]
        self.assertEqual(len(image), 1)
        self.assertIn("Figure 1", image[0].text)
        self.assertEqual(image[0].metadata.heading, "3 Model Architecture")
        self.assertEqual(image[0].metadata.page, 2)

    def test_chart_without_caption_is_skipped(self):
        blocks = [{"type": "chart", "page_idx": 12, "content": "", "captions": []}]
        self.assertEqual(chunk_content_list(blocks, "doc"), [])

    def test_table_chunk_uses_html_and_caption(self):
        blocks = [
            {"type": "paragraph_title", "page_idx": 5, "content": "4 Why Self-Attention"},
            {
                "type": "table",
                "page_idx": 5,
                "content": "| stale | markdown |",
                "captions": [{"content": "Table 1: complexity per layer."}],
                "table_body": (
                    "<table><tr><td>k</td><td>v</td></tr>"
                    "<tr><td>a</td><td>b</td></tr></table>"
                ),
            },
        ]
        chunk = chunk_content_list(blocks, "doc")[0]
        self.assertEqual(chunk.metadata.chunk_type, "table")
        self.assertIn("Table 1: complexity per layer.", chunk.text)
        self.assertIn("| a | b |", chunk.text)
        self.assertNotIn("stale", chunk.text)

    def test_table_heading_follows_referencing_section(self):
        blocks = [
            {"type": "paragraph_title", "page_idx": 6, "content": "5.4 Regularization"},
            {"type": "text", "page_idx": 6, "content": "Regression text. " * 40},
            {
                "type": "table",
                "page_idx": 7,
                "content": "",
                "captions": [{"content": "Table 2: BLEU scores."}],
                "table_body": "<table><tr><td>m</td></tr><tr><td>x</td></tr></table>",
            },
            {"type": "paragraph_title", "page_idx": 7, "content": "6.1 Machine Translation"},
            {
                "type": "text",
                "page_idx": 7,
                "content": "Table 2 summarizes our results. " * 20,
            },
        ]
        chunks = chunk_content_list(blocks, "doc")
        table = next(c for c in chunks if c.metadata.chunk_type == "table")
        self.assertEqual(table.metadata.heading, "6.1 Machine Translation")
        self.assertTrue(table.text.startswith("6.1 Machine Translation\n\n"))
        self.assertNotIn("5.4 Regularization", table.text)

    def test_empty_blocks_do_not_produce_chunks(self):
        blocks = [
            {"type": "text", "page_idx": 0, "content": "   "},
            {"type": "equation", "page_idx": 0, "content": ""},
        ]
        self.assertEqual(chunk_content_list(blocks, "doc"), [])


if __name__ == "__main__":
    unittest.main()
