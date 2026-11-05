"""Reader dispatch."""

from __future__ import annotations

from pathlib import Path

from .detect import SourceFormat, detect_format
from .docx_reader import read_docx
from .epub_reader import read_epub
from .pdf_reader import read_pdf
from .txt_reader import read_text
from .types import IngestResult


def read_source(path: str | Path) -> IngestResult:
    file = Path(path)
    source_format = detect_format(file)
    if source_format is SourceFormat.EPUB:
        return read_epub(file)
    if source_format is SourceFormat.DOCX:
        return read_docx(file)
    if source_format is SourceFormat.PDF:
        return read_pdf(file)
    if source_format is SourceFormat.MD:
        return read_text(file, markdown=True)
    return read_text(file, markdown=False)
