"""Markdown export: YAML front matter, chapter headings, verse and endnotes."""

from __future__ import annotations

import html as html_lib
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import yaml
from lxml import etree
from lxml import html as lxml_html

from tarjoman.core.atomic import atomic_write_bytes
from tarjoman.persian.markup import rebuilt_html

from .models import ExportBook, ExportChapter, ExportSegment
from .xhtml import chapter_title, footnote_id

_VOID = {"br", "img", "hr", "wbr"}
_SKIP = {"script", "style", "noscript", "template"}
_MD_SPECIAL = "\\`*_[]"


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _epub_type(element: etree._Element) -> str:
    for key, value in element.attrib.items():
        name = str(key).split("}")[-1].split(":")[-1]
        if name == "type":
            return str(value)
    return ""


def _md_text(text: str) -> str:
    escaped = html_lib.escape(text, quote=False)
    for char in _MD_SPECIAL:
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def _attr(value: str) -> str:
    return html_lib.escape(value, quote=True)


def _parse(fragment: str) -> etree._Element | None:
    if not fragment.strip():
        return None
    try:
        return lxml_html.fragment_fromstring(fragment, create_parent="div")
    except (etree.ParserError, ValueError):
        return None


def _inline_children(
    element: etree._Element, seen_noterefs: set[str]
) -> str:
    parts: list[str] = []

    def text(value: str | None) -> None:
        if value:
            parts.append(_md_text(value))

    text(element.text)
    for child in element:
        tag = _local(child.tag)
        if not tag or tag in _SKIP:
            text(child.tail)
            continue
        inner = _inline_children(child, seen_noterefs)
        if tag in {"em", "i"}:
            piece = f"*{inner}*" if inner else ""
        elif tag in {"strong", "b"}:
            piece = f"**{inner}**" if inner else ""
        elif tag == "code":
            piece = f"`{inner}`" if inner else ""
        elif tag == "br":
            piece = "  \n"
        elif tag == "a":
            href = str(child.get("href", ""))
            if href.startswith("#") and "noteref" in _epub_type(child).split():
                target = href[1:]
                if target and target not in seen_noterefs:
                    seen_noterefs.add(target)
                    piece = (
                        f'<a id="noteref-{_attr(target)}" href="{_attr(href)}">'
                        f"{inner}</a>"
                    )
                else:
                    piece = f'<a href="{_attr(href)}">{inner}</a>'
            else:
                piece = f"[{inner}]({_md_href(href)})" if _link_ok(href) else inner
        elif tag == "img":
            piece = _md_text(str(child.get("alt", "")))
        elif tag in {"sup", "sub"}:
            piece = f"<{tag}>{inner}</{tag}>"
        elif tag in _VOID:
            piece = ""
        else:
            piece = inner
        parts.append(piece)
        text(child.tail)
    return "".join(parts)


def _link_ok(href: str) -> bool:
    value = href.strip()
    if not value or any(ord(char) < 0x20 for char in value):
        return False
    lowered = value.lower()
    if any(token in lowered for token in ("javascript:", "data:", "vbscript:")):
        return False
    if value.startswith(("#", "/", "./", "../")):
        return True
    scheme = urlsplit(value).scheme.lower()
    return scheme in {"http", "https", "mailto"} or not scheme


def _md_href(href: str) -> str:
    return quote(href.strip(), safe="/#?&=:%+,;@!$'*~._-")


def _inline_markdown(fragment: str, seen_noterefs: set[str]) -> str:
    root = _parse(fragment)
    if root is None:
        return _md_text(fragment)
    return _inline_children(root, seen_noterefs)


def _verse_block(text: str) -> str:
    lines = text.rstrip("\n").split("\n")
    return "  \n".join(f"> {line}" if line else ">" for line in lines)


def _quote_block(fragment: str, seen: set[str]) -> str:
    value = _inline_markdown(fragment, seen)
    lines = value.split("\n")
    return "\n".join(f"> {line}" if line else ">" for line in lines)


def _table_cells(element: etree._Element) -> list[str]:
    cells = []
    for cell in element:
        text = " ".join("".join(cell.itertext()).split())
        if _local(cell.tag) in {"td", "th"}:
            cells.append(text.replace("|", "\\|"))
    return cells


def _table_markdown(segment: ExportSegment) -> str:
    fragment = f"<table>{rebuilt_html(segment.fa_markup, segment.nodes)}</table>"
    root = _parse(fragment)
    rows: list[list[str]] = []
    if root is not None:
        for element in root.iter():
            if _local(element.tag) == "tr":
                cells = _table_cells(element)
                if cells:
                    rows.append(cells)
    if not rows:
        return _md_text(segment.fa_text.strip())
    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(padded[0]) + " |"]
    lines.append("|" + "|".join([" --- "] * width) + "|")
    lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    return "\n".join(lines)


def _segment_markdown(segment: ExportSegment, seen: set[str]) -> str:
    fragment = rebuilt_html(segment.fa_markup, segment.nodes)
    if segment.kind == "heading":
        value = _inline_markdown(fragment, seen)
        return f"### {value}" if value else ""
    if segment.kind == "verse":
        lines = segment.fa_text.rstrip("\n").split("\n")
        if not any(line.strip() for line in lines):
            return ""
        return _verse_block(segment.fa_text)
    if segment.kind == "quote":
        return _quote_block(fragment, seen)
    if segment.kind == "caption":
        value = _inline_markdown(fragment, seen)
        return f"*{value}*" if value else ""
    if segment.kind == "table":
        return _table_markdown(segment)
    return _inline_markdown(fragment, seen)


def _front_matter(book: ExportBook) -> str:
    data: dict[str, Any] = {
        "title": book.title_fa or book.title_en,
        "author": book.author,
        "translator": book.translator,
        "lang": "fa",
        "dir": "rtl",
        "source_title": book.metadata.get("title") or book.title_en,
        "rights_basis": book.rights_basis,
    }
    rendered = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{rendered}\n---"


def _chapter_markdown(chapter: ExportChapter, seen: set[str]) -> str:
    blocks = [f"## {chapter_title(chapter)}"]
    for segment in chapter.segments:
        if segment.kind == "footnote":
            continue
        value = _segment_markdown(segment, seen)
        if value.strip():
            blocks.append(value)
    return "\n\n".join(blocks)


def _endnotes(book: ExportBook, seen: set[str]) -> str:
    entries: list[str] = []
    for chapter in book.chapters:
        for segment in chapter.segments:
            if segment.kind != "footnote":
                continue
            anchor = footnote_id(segment)
            text = _inline_markdown(
                rebuilt_html(segment.fa_markup, segment.nodes), seen
            )
            backlink = f"[↩](#noteref-{anchor})"
            entries.append(f'{len(entries) + 1}. <a id="{_attr(anchor)}"></a>{text} {backlink}')
    if not entries:
        return ""
    return "## یادداشت‌ها\n\n" + "\n".join(entries)


def write_markdown(book: ExportBook, path: Path) -> Path:
    seen: set[str] = set()
    parts = [_front_matter(book)]
    parts.extend(_chapter_markdown(chapter, seen) for chapter in book.chapters)
    endnotes = _endnotes(book, seen)
    if endnotes:
        parts.append(endnotes)
    return atomic_write_bytes(path, ("\n\n".join(parts).rstrip() + "\n").encode("utf-8"))
