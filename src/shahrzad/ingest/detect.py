"""Format sniffing by magic bytes, not extension."""

from __future__ import annotations

import zipfile
from enum import StrEnum
from pathlib import Path

from shahrzad.core.errors import UsageError


class SourceFormat(StrEnum):
    EPUB = "epub"
    DOCX = "docx"
    PDF = "pdf"
    TXT = "txt"
    MD = "md"


def _zip_members(path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(path) as archive:
            return set(archive.namelist())
    except zipfile.BadZipFile:
        return set()


def detect_format(path: str | Path) -> SourceFormat:
    file = Path(path)
    if not file.is_file():
        raise UsageError(f"input file not found: {file}")
    with file.open("rb") as handle:
        magic = handle.read(8)
    if magic.startswith(b"%PDF"):
        return SourceFormat.PDF
    if magic.startswith(b"PK\x03\x04"):
        members = _zip_members(file)
        if "META-INF/container.xml" in members or "mimetype" in members:
            return SourceFormat.EPUB
        if "word/document.xml" in members:
            return SourceFormat.DOCX
        raise UsageError(f"{file} is a ZIP archive but not an EPUB or DOCX")
    suffix = file.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return SourceFormat.MD
    with file.open("rb") as handle:
        raw = handle.read(4096)
    for encoding in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            raw.decode(encoding)
            return SourceFormat.TXT
        except UnicodeDecodeError:
            continue
    raise UsageError(f"unsupported source format: {file}")
