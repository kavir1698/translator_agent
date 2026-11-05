"""Plain-text export for diffing and downstream read-through pipelines."""

from __future__ import annotations

from pathlib import Path

from tarjoman.core.atomic import atomic_write_bytes

from .models import ExportBook, ExportChapter
from .xhtml import chapter_title, footnote_id


def _chapter_text(chapter: ExportChapter) -> str:
    blocks = [f"# {chapter_title(chapter)}"]
    for segment in chapter.segments:
        if segment.kind == "footnote":
            continue
        text = segment.fa_text.strip()
        if not text:
            continue
        if segment.kind == "heading":
            blocks.append(f"# {text}")
        else:
            blocks.append(text)
    return "\n\n".join(blocks)


def _endnotes(book: ExportBook) -> str:
    entries: list[str] = []
    for chapter in book.chapters:
        for segment in chapter.segments:
            if segment.kind != "footnote":
                continue
            text = segment.fa_text.strip()
            if not text:
                continue
            anchor = footnote_id(segment)
            entries.append(f"[{len(entries) + 1}] {text} ({anchor})")
    if not entries:
        return ""
    return "یادداشت‌ها\n\n" + "\n".join(entries)


def write_plain_text(book: ExportBook, path: Path) -> Path:
    parts = [_chapter_text(chapter) for chapter in book.chapters]
    endnotes = _endnotes(book)
    if endnotes:
        parts.append(endnotes)
    return atomic_write_bytes(path, ("\n\n".join(parts).rstrip() + "\n").encode("utf-8"))
