"""Deterministic chapterization (plan 6.0.4), with optional structure.yaml overrides."""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from tarjoman.core.errors import ConfigError
from tarjoman.core.models import ChapterKind

from .blocks import BACK_TYPES, FRONT_TYPES, Block, BlockKind
from .normalize_source import normalize_source
from .types import Document, IngestResult

_MIN_CHAPTER_WORDS = 40


@dataclass
class ChapterPlan:
    kind: ChapterKind
    title_en: str | None
    blocks: list[Block] = field(default_factory=list)
    source_doc: str | None = None
    skip_translation: bool = False

    @property
    def word_count(self) -> int:
        return sum(len(block.text.split()) for block in self.blocks)


def _kind_for_document(document: Document, index: int, total: int) -> ChapterKind:
    etype = document.epub_type.lower()
    if etype in FRONT_TYPES or etype in BACK_TYPES:
        return ChapterKind.FRONT if etype in FRONT_TYPES else ChapterKind.BACK
    if index == 0 and etype in {"cover", "titlepage"}:
        return ChapterKind.FRONT
    if total > 1 and index == total - 1 and etype in {"backmatter", "colophon", "index"}:
        return ChapterKind.BACK
    return ChapterKind.BODY


def _heading_level(block: Block) -> int:
    if block.kind is not BlockKind.HEADING:
        return 0
    try:
        return int(block.tag[1])
    except (ValueError, IndexError):
        return 1


def _split_document(document: Document, kind: ChapterKind) -> list[ChapterPlan]:
    headings = [index for index, block in enumerate(document.blocks) if _heading_level(block) in (1, 2)]
    if not headings:
        title = document.title or next(
            (block.text for block in document.blocks if block.kind is BlockKind.HEADING), None
        )
        return [
            ChapterPlan(kind=kind, title_en=title, blocks=list(document.blocks), source_doc=document.id)
        ]

    chapters: list[ChapterPlan] = []
    boundaries = headings if headings[0] == 0 else [0, *headings]
    for position, start in enumerate(boundaries):
        end = boundaries[position + 1] if position + 1 < len(boundaries) else len(document.blocks)
        chunk = document.blocks[start:end]
        if not chunk:
            continue
        first_heading = next((block for block in chunk if block.kind is BlockKind.HEADING), None)
        title = first_heading.text if first_heading is not None else document.title
        chapters.append(
            ChapterPlan(kind=kind, title_en=title, blocks=list(chunk), source_doc=document.id)
        )
    return chapters


def _merge_tiny(chapters: list[ChapterPlan]) -> list[ChapterPlan]:
    merged: list[ChapterPlan] = []
    pending: ChapterPlan | None = None
    for chapter in chapters:
        if pending is not None:
            chapter.blocks = pending.blocks + chapter.blocks
            if chapter.title_en is None:
                chapter.title_en = pending.title_en
            pending = None
        if chapter.word_count < _MIN_CHAPTER_WORDS and chapter.kind is not ChapterKind.FRONT:
            pending = chapter
            continue
        merged.append(chapter)
    if pending is not None:
        merged.append(pending)
    return [chapter for chapter in merged if chapter.blocks]


def _apply_structure(result: IngestResult, structure: dict[str, Any]) -> list[ChapterPlan]:
    entries = structure.get("chapters") or []
    if not entries:
        raise ConfigError("structure.yaml has no chapters")
    flat = result.all_blocks()
    normalized = [normalize_source(block.text) for block in flat]
    starts: list[int] = []
    used: set[int] = set()
    for entry in entries:
        needle = normalize_source(str(entry["start_text"]))
        matches = [index for index, text in enumerate(normalized) if text.startswith(needle)]
        if len(matches) != 1:
            near = difflib.get_close_matches(needle, normalized, n=3, cutoff=0.4)
            raise ConfigError(
                f"structure.yaml start_text matched {len(matches)} blocks: {entry['start_text']!r}",
                hint=f"near matches: {near}" if near else "check the exact opening words",
            )
        if matches[0] in used:
            raise ConfigError(f"structure.yaml reuses the same start block: {entry['start_text']!r}")
        used.add(matches[0])
        starts.append(matches[0])
    if starts != sorted(starts):
        raise ConfigError("structure.yaml chapter order is not increasing")

    chapters: list[ChapterPlan] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(flat)
        entry = entries[position]
        kind = ChapterKind(str(entry.get("kind", "body")))
        chapters.append(
            ChapterPlan(
                kind=kind,
                title_en=entry.get("title_en"),
                blocks=list(flat[start:end]),
                source_doc=None,
            )
        )
    if starts and starts[0] > 0:
        chapters[0].blocks = list(flat[: starts[0]]) + chapters[0].blocks
    return chapters


def load_structure(path: str | Path) -> dict[str, Any]:
    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"structure.yaml not found: {file}")
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"structure.yaml is not a mapping: {file}")
    return data


def chapterize(
    result: IngestResult,
    *,
    structure: dict[str, Any] | None = None,
) -> list[ChapterPlan]:
    if structure is not None:
        chapters = _apply_structure(result, structure)
    else:
        chapters = []
        total = len(result.documents)
        for index, document in enumerate(result.documents):
            kind = _kind_for_document(document, index, total)
            chapters.extend(_split_document(document, kind))
        if not chapters:
            chapters = [ChapterPlan(kind=ChapterKind.BODY, title_en=result.title, blocks=[])]
        chapters = _merge_tiny(chapters)

    for chapter in chapters:
        translatable = [block for block in chapter.blocks if block.text.strip()]
        if not translatable and chapter.kind is ChapterKind.FRONT:
            chapter.skip_translation = True
    for index, chapter in enumerate(chapters, start=1):
        if chapter.title_en is None:
            chapter.title_en = result.title if len(chapters) == 1 else f"Chapter {index}"
    return chapters
