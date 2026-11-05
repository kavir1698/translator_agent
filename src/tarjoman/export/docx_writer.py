"""RTL DOCX writer built on python-docx (cover, chapters, endnotes)."""

from __future__ import annotations

import contextlib
import os
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from docx.document import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.image.exceptions import UnrecognizedImageError
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Mm, Pt
from lxml import etree
from lxml import html as lxml_html

from tarjoman.core.config import Settings
from tarjoman.persian.markup import rebuilt_html

from .assets import ImageAsset, SourceAssets, find_asset
from .models import ExportBook, ExportChapter, ExportSegment
from .xhtml import chapter_title, heading_level

_PAGE_SIZES = {
    "a5": (Mm(148), Mm(210)),
    "a4": (Mm(210), Mm(297)),
    "letter": (Inches(8.5), Inches(11)),
}
_SKIP = {"script", "style", "noscript", "template", "head"}
_BIDI_SUCCESSORS = (
    "w:adjustRightInd",
    "w:snapToGrid",
    "w:spacing",
    "w:ind",
    "w:contextualSpacing",
    "w:mirrorIndents",
    "w:suppressOverlap",
    "w:jc",
    "w:textDirection",
    "w:textAlignment",
    "w:textboxTightWrap",
    "w:outlineLvl",
    "w:divId",
    "w:cnfStyle",
    "w:rPr",
    "w:sectPr",
    "w:pPrChange",
)
_HEADING_SIZES = {1: 32, 2: 28, 3: 26, 4: 24, 5: 24, 6: 24}
_DEFAULT_SIZE = 22
_MAX_IMAGE_WIDTH = Mm(120)
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _add_bidi(pPr: Any) -> None:
    if pPr.find(qn("w:bidi")) is None:
        pPr.insert_element_before(OxmlElement("w:bidi"), *_BIDI_SUCCESSORS)


def _set_rfonts(rPr: Any, font: str) -> None:
    fonts = rPr.get_or_add_rFonts()
    for attribute in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme"):
        key = qn(attribute)
        if key in fonts.attrib:
            del fonts.attrib[key]
    fonts.set(qn("w:ascii"), font)
    fonts.set(qn("w:hAnsi"), font)
    fonts.set(qn("w:cs"), font)


def _set_size(rPr: Any, half_points: int) -> None:
    size = rPr.get_or_add_sz()
    size.set(qn("w:val"), str(half_points))
    size_cs = rPr.find(qn("w:szCs"))
    if size_cs is None:
        size_cs = OxmlElement("w:szCs")
        size.addnext(size_cs)
    size_cs.set(qn("w:val"), str(half_points))


def _set_bidi_lang(rPr: Any) -> None:
    lang = rPr.find(qn("w:lang"))
    if lang is None:
        lang = OxmlElement("w:lang")
        rPr.insert_element_before(
            lang, "w:eastAsianLayout", "w:specVanish", "w:oMath"
        )
    lang.set(qn("w:bidi"), "fa")


def _format_run(
    run: Any,
    font: str,
    half_points: int = _DEFAULT_SIZE,
    *,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
) -> None:
    rPr = run._r.get_or_add_rPr()
    _set_rfonts(rPr, font)
    _set_size(rPr, half_points)
    rPr.get_or_add_rtl()
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    if underline:
        run.underline = True


def _format_paragraph(paragraph: Any) -> None:
    _add_bidi(paragraph._p.get_or_add_pPr())


def _parse(fragment: str) -> etree._Element | None:
    if not fragment or not fragment.strip():
        return None
    try:
        return lxml_html.fragment_fromstring(fragment, create_parent="div")
    except (etree.ParserError, ValueError):
        return None


def _add_run(paragraph: Any, text: str, font: str, half_points: int, **flags: bool) -> None:
    if not text:
        return
    run = paragraph.add_run(text)
    _format_run(run, font, half_points, **flags)


def _add_image_run(
    paragraph: Any,
    asset: ImageAsset,
    alt: str,
    font: str,
    half_points: int,
) -> None:
    run = paragraph.add_run()
    try:
        shape = run.add_picture(BytesIO(asset.data))
    except (UnrecognizedImageError, OSError, ValueError):
        run.add_text(alt)
    else:
        if shape.width and int(shape.width) > int(_MAX_IMAGE_WIDTH):
            ratio = int(_MAX_IMAGE_WIDTH) / int(shape.width)
            shape.width = _MAX_IMAGE_WIDTH
            shape.height = int(int(shape.height) * ratio)
    _format_run(run, font, half_points)


def _walk_inline(
    element: etree._Element,
    paragraph: Any,
    assets: SourceAssets | None,
    font: str,
    half_points: int,
    *,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
) -> None:
    _add_run(paragraph, element.text or "", font, half_points, bold=bold, italic=italic, underline=underline)
    for child in element:
        tag = _local(child.tag)
        if tag in _SKIP:
            _add_run(paragraph, child.tail or "", font, half_points, bold=bold, italic=italic, underline=underline)
            continue
        if tag == "br":
            run = paragraph.add_run()
            run.add_break()
            _format_run(run, font, half_points, bold=bold, italic=italic, underline=underline)
        elif tag == "img":
            asset = find_asset(assets, str(child.get("src", ""))) if assets is not None else None
            if asset is None:
                _add_run(
                    paragraph,
                    str(child.get("alt", "")),
                    font,
                    half_points,
                    bold=bold,
                    italic=italic,
                    underline=underline,
                )
            else:
                _add_image_run(paragraph, asset, str(child.get("alt", "")), font, half_points)
        else:
            _walk_inline(
                child,
                paragraph,
                assets,
                font,
                half_points,
                bold=bold or tag in {"strong", "b"},
                italic=italic or tag in {"em", "i"},
                underline=underline or tag == "u",
            )
        _add_run(paragraph, child.tail or "", font, half_points, bold=bold, italic=italic, underline=underline)


def _render_inline(
    paragraph: Any,
    fragment: str,
    assets: SourceAssets | None,
    font: str,
    half_points: int,
) -> None:
    root = _parse(fragment)
    if root is None:
        _add_run(paragraph, fragment, font, half_points)
        return
    _walk_inline(root, paragraph, assets, font, half_points)


def _page_break(document: DocxDocument, font: str) -> None:
    paragraph = document.add_paragraph()
    _format_paragraph(paragraph)
    run = paragraph.add_run()
    run.add_break(WD_BREAK.PAGE)
    _format_run(run, font)


def _heading_paragraph(document: DocxDocument, level: int, font: str) -> Any:
    paragraph = document.add_paragraph(style=f"Heading {min(max(level, 1), 9)}")
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    _format_paragraph(paragraph)
    return paragraph


def _add_heading(
    document: DocxDocument, text: str, level: int, font: str
) -> Any:
    paragraph = _heading_paragraph(document, level, font)
    half_points = _HEADING_SIZES.get(level, _DEFAULT_SIZE)
    _add_run(paragraph, text, font, half_points, bold=True)
    return paragraph


def _table_rows(fragment: str) -> list[list[etree._Element]]:
    root = _parse(fragment)
    if root is None:
        return []
    rows: list[list[etree._Element]] = []
    for element in root.iter():
        if _local(element.tag) != "tr":
            continue
        cells = [child for child in element if _local(child.tag) in {"td", "th"}]
        if cells:
            rows.append(cells)
    return rows


def _write_table(
    document: DocxDocument,
    segment: ExportSegment,
    assets: SourceAssets | None,
    font: str,
) -> None:
    fragment = f"<table>{rebuilt_html(segment.fa_markup, segment.nodes)}</table>"
    rows = _table_rows(fragment)
    if not rows:
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _format_paragraph(paragraph)
        _add_run(paragraph, segment.fa_text, font, _DEFAULT_SIZE)
        return
    width = max(len(row) for row in rows)
    table = document.add_table(rows=len(rows), cols=width)
    with contextlib.suppress(KeyError):
        table.style = "Table Grid"
    for row_index, row in enumerate(rows):
        for column_index, cell in enumerate(row):
            paragraph = table.cell(row_index, column_index).paragraphs[0]
            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            _format_paragraph(paragraph)
            _walk_inline(
                cell,
                paragraph,
                assets,
                font,
                _DEFAULT_SIZE,
                bold=_local(cell.tag) == "th",
            )


def _write_segment(
    document: DocxDocument,
    segment: ExportSegment,
    assets: SourceAssets | None,
    settings: Settings,
) -> None:
    font = settings.export.epub.font_body
    if segment.kind == "heading":
        level = min(max(heading_level(segment.source_html), 1), 9)
        paragraph = _heading_paragraph(document, level, font)
        _render_inline(
            paragraph,
            rebuilt_html(segment.fa_markup, segment.nodes),
            assets,
            font,
            _HEADING_SIZES.get(level, _DEFAULT_SIZE),
        )
        return
    if segment.kind == "table":
        _write_table(document, segment, assets, font)
        return
    paragraph = document.add_paragraph()
    _format_paragraph(paragraph)
    if segment.kind == "verse":
        paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    elif segment.kind == "quote":
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        paragraph.paragraph_format.left_indent = Mm(12)
        paragraph.paragraph_format.right_indent = Mm(12)
    elif segment.kind == "caption":
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.style = document.styles["Caption"]
    else:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    _render_inline(
        paragraph,
        rebuilt_html(segment.fa_markup, segment.nodes),
        assets,
        font,
        _DEFAULT_SIZE,
    )
    if segment.kind == "caption":
        for run in paragraph.runs:
            run.italic = True


def _write_endnotes(
    document: DocxDocument,
    chapter: ExportChapter,
    assets: SourceAssets | None,
    settings: Settings,
) -> None:
    if not settings.export.docx.endnotes:
        return
    footnotes = [segment for segment in chapter.segments if segment.kind == "footnote"]
    if not footnotes:
        return
    font = settings.export.epub.font_body
    _add_heading(document, "یادداشت‌ها", 2, font)
    for number, segment in enumerate(footnotes, start=1):
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        _format_paragraph(paragraph)
        _add_run(paragraph, f"{number}. ", font, _DEFAULT_SIZE)
        _render_inline(
            paragraph,
            rebuilt_html(segment.fa_markup, segment.nodes),
            assets,
            font,
            _DEFAULT_SIZE,
        )
        for run in paragraph.runs:
            if run.text == f"{number}. ":
                run.bold = True


def _configure_styles(document: DocxDocument, font: str) -> None:
    styles = document.styles.element
    doc_defaults = styles.find(qn("w:docDefaults"))
    if doc_defaults is not None:
        rpr_default = doc_defaults.find(qn("w:rPrDefault"))
        if rpr_default is not None:
            rpr = rpr_default.find(qn("w:rPr"))
            if rpr is not None:
                _set_rfonts(rpr, font)
                _set_size(rpr, _DEFAULT_SIZE)
                _set_bidi_lang(rpr)
        ppr_default = doc_defaults.find(qn("w:pPrDefault"))
        if ppr_default is not None:
            ppr = ppr_default.find(qn("w:pPr"))
            if ppr is not None:
                _add_bidi(ppr)

    normal = document.styles["Normal"]
    normal.font.name = font
    normal.font.size = Pt(11)
    normal_rpr = normal.element.get_or_add_rPr()
    _set_rfonts(normal_rpr, font)
    _set_size(normal_rpr, _DEFAULT_SIZE)
    normal_rpr.get_or_add_rtl()
    _add_bidi(normal.element.get_or_add_pPr())

    for level in range(1, 7):
        try:
            style = document.styles[f"Heading {level}"]
        except KeyError:
            continue
        style.font.name = font
        style_rpr = style.element.get_or_add_rPr()
        _set_rfonts(style_rpr, font)
        _set_size(style_rpr, _HEADING_SIZES[level])
        style_rpr.get_or_add_rtl()
        _add_bidi(style.element.get_or_add_pPr())


def _configure_page(document: DocxDocument, settings: Settings) -> None:
    name = settings.export.docx.page.strip().lower()
    width, height = _PAGE_SIZES.get(name, _PAGE_SIZES["a5"])
    section = document.sections[0]
    section.page_width = width
    section.page_height = height
    section.top_margin = Mm(18)
    section.bottom_margin = Mm(18)
    section.left_margin = Mm(16)
    section.right_margin = Mm(16)

    settings_element = document.settings.element
    if settings_element.find(qn("w:mirrorMargins")) is None:
        settings_element.insert_element_before(
            OxmlElement("w:mirrorMargins"),
            "w:proofState",
            "w:defaultTabStop",
            "w:characterSpacingControl",
            "w:compat",
            "w:docVars",
        )


def _configure_core(document: DocxDocument, book: ExportBook) -> None:
    properties = document.core_properties
    properties.title = book.title_fa or book.title_en
    properties.author = book.author or ""
    properties.last_modified_by = book.translator or ""
    if book.translator:
        properties.comments = f"Persian translation by {book.translator}"
    try:
        stamp = datetime.fromisoformat(book.generated_at.replace("Z", "+00:00"))
    except ValueError:
        return
    properties.created = stamp
    properties.modified = stamp


def _add_cover(
    document: DocxDocument,
    book: ExportBook,
    assets: SourceAssets,
    font: str,
) -> None:
    cover = assets.cover
    if cover is None:
        return
    paragraph = document.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _format_paragraph(paragraph)
    run = paragraph.add_run()
    try:
        run.add_picture(BytesIO(cover.data), width=Mm(120))
    except (UnrecognizedImageError, OSError, ValueError):
        run.add_text(book.title_fa or book.title_en)
    _format_run(run, font)
    _page_break(document, font)


def _write_chapter(
    document: DocxDocument,
    chapter: ExportChapter,
    assets: SourceAssets | None,
    settings: Settings,
) -> None:
    font = settings.export.epub.font_body
    title = chapter_title(chapter)
    _add_heading(document, title, 1, font)
    skipping_title = (
        bool(chapter.segments)
        and chapter.segments[0].kind == "heading"
        and chapter.segments[0].fa_text.strip() == title
    )
    for position, segment in enumerate(chapter.segments):
        if skipping_title and position == 0:
            continue
        if segment.kind == "footnote":
            continue
        _write_segment(document, segment, assets, settings)
    _write_endnotes(document, chapter, assets, settings)


def _finalize_rtl(document: DocxDocument, settings: Settings) -> None:
    font = settings.export.epub.font_body
    body = document.element.body
    for paragraph in body.iter(qn("w:p")):
        ppr = paragraph.find(qn("w:pPr"))
        if ppr is None:
            ppr = OxmlElement("w:pPr")
            paragraph.insert(0, ppr)
        _add_bidi(ppr)
        for run in paragraph.findall(qn("w:r")):
            rpr = run.find(qn("w:rPr"))
            if rpr is None:
                rpr = OxmlElement("w:rPr")
                run.insert(0, rpr)
            _set_rfonts(rpr, font)
            rpr.get_or_add_rtl()
            size = rpr.find(qn("w:sz"))
            value = size.get(qn("w:val")) if size is not None else None
            _set_size(rpr, int(value) if value is not None else _DEFAULT_SIZE)


def _write_deterministic_archive(source_path: Path, target_path: Path) -> None:
    with zipfile.ZipFile(source_path) as source, zipfile.ZipFile(target_path, "w") as target:
        for original in source.infolist():
            info = zipfile.ZipInfo(original.filename, date_time=_ZIP_TIME)
            info.compress_type = original.compress_type
            info.external_attr = 0o644 << 16
            target.writestr(info, source.read(original.filename))


def write_docx(
    book: ExportBook,
    path: Path,
    settings: Settings,
    assets: SourceAssets | None = None,
) -> Path:
    """Write an RTL Word document with chapter endnotes and stable properties."""
    document = Document()
    font = settings.export.epub.font_body
    _configure_styles(document, font)
    _configure_page(document, settings)
    _configure_core(document, book)
    if assets is not None:
        _add_cover(document, book, assets, font)
    for position, chapter in enumerate(book.chapters):
        if position > 0:
            _page_break(document, font)
        _write_chapter(document, chapter, assets, settings)
    _finalize_rtl(document, settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_name(f".{path.name}.raw.tmp")
    temp = path.with_name(f".{path.name}.tmp")
    raw.unlink(missing_ok=True)
    temp.unlink(missing_ok=True)
    try:
        document.save(str(raw))
        _write_deterministic_archive(raw, temp)
        os.replace(temp, path)
    finally:
        raw.unlink(missing_ok=True)
        temp.unlink(missing_ok=True)
    return path
