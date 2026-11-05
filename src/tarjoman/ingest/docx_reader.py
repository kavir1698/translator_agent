"""DOCX reader via python-docx, producing the shared HTML block stream."""

from __future__ import annotations

import html
import re
from pathlib import Path

from docx import Document as load_docx
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import html as lxml_html

from .blocks import BlockKind, iter_blocks
from .types import Document, IngestResult

_HEADING = re.compile(r"^(heading|title)\s*([1-6])?$", re.IGNORECASE)


def _run_html(run: object) -> str:
    text = html.escape(str(getattr(run, "text", "")), quote=False).replace("\n", "<br/>")
    if not text:
        return ""
    bold = bool(getattr(run, "bold", False))
    italic = bool(getattr(run, "italic", False))
    underline = bool(getattr(run, "underline", False))
    if italic:
        text = f"<em>{text}</em>"
    if bold:
        text = f"<strong>{text}</strong>"
    if underline:
        text = f"<u>{text}</u>"
    return text


def _paragraph_html(paragraph: Paragraph) -> str:
    style = (paragraph.style.name if paragraph.style is not None else "") or ""
    body = "".join(_run_html(run) for run in paragraph.runs)
    if not body:
        body = html.escape(paragraph.text, quote=False)
    match = _HEADING.match(style.strip())
    if match:
        level = match.group(2) or "1"
        return f"<h{level}>{body}</h{level}>"
    if "quote" in style.lower() or "epigraph" in style.lower():
        return f"<blockquote>{body}</blockquote>"
    return f"<p>{body}</p>"


def _table_html(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            text = " ".join(paragraph.text for paragraph in cell.paragraphs).strip()
            cells.append(f"<td>{html.escape(text, quote=False)}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table>{''.join(rows)}</table>"


def _local(tag: object) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1].lower() if "}" in text else text.lower()


def read_docx(path: str | Path) -> IngestResult:
    document = load_docx(str(path))
    fragments: list[str] = []
    notes: list[str] = []
    for child in document.element.body.iterchildren():
        tag = _local(child.tag)
        if tag == "p":
            fragments.append(_paragraph_html(Paragraph(child, document)))
        elif tag == "tbl":
            fragments.append(_table_html(Table(child, document)))
        elif tag == "sectPr":
            continue
        else:
            notes.append(f"unsupported DOCX element skipped: {tag}")

    wrapper = lxml_html.fragment_fromstring("".join(fragments), create_parent="div")
    blocks = iter_blocks(wrapper)
    title = document.core_properties.title or next(
        (block.text for block in blocks if block.kind is BlockKind.HEADING), None
    )
    author = document.core_properties.author or None
    return IngestResult(
        source_format="docx",
        documents=[Document(id="doc001", blocks=blocks, title=title)],
        title=title or Path(path).stem,
        author=author,
        metadata={"docx_notes": notes} if notes else {},
    )
