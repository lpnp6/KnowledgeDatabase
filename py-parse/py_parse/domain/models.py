"""Core domain models for chunking."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChunkMetadata:
    """Metadata attached to each output chunk (filtering + provenance)."""

    page: int
    heading: str  # nearest title text; empty if none
    chunk_type: str  # "text" | "table" | "code" | "image"


@dataclass
class Chunk:
    chunk_id: str
    text: str
    metadata: ChunkMetadata


@dataclass
class DocMeta:
    total_pages: int
    title: str = ""
    warning: str = ""


@dataclass
class ParseOutput:
    doc_meta: DocMeta
    chunks: list[Chunk]
