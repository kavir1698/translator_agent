"""Shared ingest data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .blocks import Block


@dataclass
class Document:
    id: str
    blocks: list[Block]
    title: str | None = None
    epub_type: str = ""
    href: str | None = None


@dataclass
class IngestResult:
    source_format: str
    documents: list[Document]
    title: str
    author: str | None = None
    language: str | None = None
    publisher: str | None = None
    rights: str | None = None
    identifier: str | None = None
    cover: str | None = None
    import_quality: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def all_blocks(self) -> list[Block]:
        return [block for document in self.documents for block in document.blocks]
