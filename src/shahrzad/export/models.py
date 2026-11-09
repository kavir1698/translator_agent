"""Read-only export model shared by every output format."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ExportSegment:
    id: str
    idx: int
    kind: str
    source_text: str
    source_html: str
    nodes: list[dict[str, Any]]
    fa_text: str
    fa_markup: str
    notes: dict[str, Any]
    issues: list[dict[str, Any]]


@dataclass
class ExportChapter:
    idx: int
    kind: str
    title_en: str | None
    title_fa: str | None
    segments: list[ExportSegment] = field(default_factory=list)
    source_doc: str | None = None


@dataclass
class ExportBook:
    slug: str
    title_en: str
    title_fa: str | None
    author: str | None
    translator: str | None
    language: str
    rights_basis: str
    source_format: str
    source_sha256: str
    source_path: Path
    metadata: dict[str, Any]
    cover: str | None
    chapters: list[ExportChapter]
    generated_at: str
    incomplete: bool
    open_errors: int
