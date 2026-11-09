"""Deterministic EPUB 3 writer: minimal OPF/nav/XHTML with lxml + Jinja."""

from __future__ import annotations

import html as html_lib
import os
import posixpath
import re
import shutil
import subprocess
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from jinja2 import Environment, FileSystemLoader
from lxml import etree

from shahrzad import PROJECT_URL
from shahrzad.core.config import Settings
from shahrzad.core.errors import RuntimeFailure
from shahrzad.core.logging import get_logger

from .assets import ImageAsset, SourceAssets
from .models import ExportBook, ExportChapter
from .structure import build_export_structure
from .xhtml import (
    chapter_title,
    footnote_id,
    isolate_latin,
    render_segment,
    segment_body,
)

_ASSET_DIR = Path(__file__).with_name("epub_assets")
_TEMPLATE_DIR = _ASSET_DIR / "templates"
_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
    keep_trailing_newline=True,
)
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_REMOTE = re.compile(r"\b(?:src|href)\s*=\s*[\"'](?:https?:)?//", re.IGNORECASE)
_SCRIPT = re.compile(r"<script\b", re.IGNORECASE)
_ACTIVE = re.compile(
    r"<(?:script|iframe|object|embed|base|form|input)\b"
    r"|\son[a-z]+\s*="
    r"|\b(?:href|src)\s*=\s*[\"']\s*(?:javascript|data|vbscript):",
    re.IGNORECASE,
)
_A_TAG = re.compile(r"<a\b[^>]*>", re.IGNORECASE)
_HREF = re.compile(r'\bhref="(#[^"]+)"', re.IGNORECASE)
_ID_ATTR = re.compile(r'\sid="([^"]*)"', re.IGNORECASE)
_INTERNAL_HREF = re.compile(r'href="#([^"]*)"', re.IGNORECASE)
_LOGGER = get_logger("export.epub")


def _render(name: str, **context: Any) -> str:
    return _ENV.get_template(name).render(**context)


def _escape(value: str) -> str:
    return html_lib.escape(value, quote=False)


def _attr_escape(value: str) -> str:
    return html_lib.escape(value, quote=True)


def _identifier(book: ExportBook) -> str:
    value = uuid.uuid5(uuid.NAMESPACE_URL, f"{book.slug}\x1f{book.source_sha256}")
    return f"urn:uuid:{value}"


def _chapter_file(idx: int) -> str:
    return f"text/chapter-{idx:03d}.xhtml"


def _add_noteref_ids(body: str, seen: dict[str, str]) -> str:
    """Give each noteref a stable local id and map footnote id -> referrer id.

    Source EPUBs usually already give the referrer anchor an id (for example
    ``_footnote_referrer_...``). Appending a second ``id`` made the XHTML
    ill-formed, so keep the existing one and remember it for the backlink.
    """

    def replace(match: re.Match[str]) -> str:
        tag = match.group(0)
        if "noteref" not in tag:
            return tag
        href = _HREF.search(tag)
        if href is None:
            return tag
        target = href.group(1)[1:]
        if not target or target in seen:
            return tag
        existing = _ID_ATTR.search(tag)
        if existing:
            seen[target] = existing.group(1)
            return tag
        anchor = f"noteref-{target}"
        seen[target] = anchor
        return tag[:-1] + f' id="{_attr_escape(anchor)}">'

    return _A_TAG.sub(replace, body)


def _rewrite_source_links(fragment: str, link_map: dict[str, str]) -> str:
    """Point links that name source XHTML files at the regrouped EPUB files."""
    if not link_map:
        return fragment

    def replace_href(match: re.Match[str]) -> str:
        value = match.group(1)
        if value.startswith(
            ("#", "http://", "https://", "mailto:", "data:", "tel:")
        ):
            return match.group(0)
        base, separator, fragment_id = value.partition("#")
        target = link_map.get(PurePosixPath(base.replace("\\", "/")).name)
        if target is None:
            return match.group(0)
        suffix = f"#{fragment_id}" if separator else ""
        return f'href="{_attr_escape(target)}{suffix}"'

    return re.sub(r'href="([^"]+)"', replace_href, fragment)


def _fix_dangling_internal_hrefs(fragment: str) -> str:
    """Point same-document links that have no local target at the chapter top.

    A footnote can live in a chapter after the chapter that holds its referrer
    (for example when one source document is split into several chapters).
    The reference validator requires every ``href="#..."`` to resolve locally.
    """

    ids = set(re.findall(r'\bid="([^"]+)"', fragment))

    def replace(match: re.Match[str]) -> str:
        target = match.group(1)
        if target == "chapter-top" or target in ids:
            return match.group(0)
        return 'href="#chapter-top"'

    return _INTERNAL_HREF.sub(replace, fragment)


def _chapter_body(
    chapter: ExportChapter,
    assets: SourceAssets,
    *,
    link_map: dict[str, str] | None = None,
    toc_idx: int | None = None,
    toc_items: list[tuple[str, str]] | None = None,
) -> str:
    title = chapter_title(chapter)
    if toc_idx is not None and chapter.idx == toc_idx and toc_items is not None:
        entries = "\n".join(
            f'<li><a href="{_attr_escape(href)}">{isolate_latin(label)}</a></li>'
            for label, href in toc_items
        )
        return (
            f'<h1 class="chapter-title">{isolate_latin(title)}</h1>\n'
            f'<ul class="toc">\n{entries}\n</ul>'
        )
    parts = [f'<h1 class="chapter-title">{isolate_latin(title)}</h1>']
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
        rendered = render_segment(segment, assets)
        if rendered:
            parts.append(rendered)

    seen: dict[str, str] = {}
    body = _add_noteref_ids("\n".join(parts), seen)

    footnotes = [segment for segment in chapter.segments if segment.kind == "footnote"]
    if footnotes:
        items: list[str] = []
        used: set[str] = set()
        for segment in footnotes:
            anchor = footnote_id(segment)
            while anchor in used:
                anchor = f"{anchor}-2"
            used.add(anchor)
            text = segment_body(segment, assets) or "&#160;"
            target = f"#{seen[anchor]}" if anchor in seen else "#chapter-top"
            items.append(
                f'<li id="{_attr_escape(anchor)}" epub:type="endnote" role="doc-endnote">'
                f'<p>{text} <a class="backlink" href="{_attr_escape(target)}" '
                'role="doc-backlink">↩</a></p></li>'
            )
        body += (
            '\n<section class="endnotes" epub:type="endnotes" role="doc-endnotes">\n'
            "<h2>یادداشت‌ها</h2>\n<ol>\n"
            + "\n".join(items)
            + "\n</ol>\n</section>"
        )
    body = _rewrite_source_links(body, link_map or {})
    return _fix_dangling_internal_hrefs(body)


def _manifest(
    book: ExportBook, assets: SourceAssets, *, include_incomplete: bool
) -> tuple[list[dict[str, str]], list[str]]:
    items: list[dict[str, str]] = [
        {
            "id": "nav",
            "href": "nav.xhtml",
            "media_type": "application/xhtml+xml",
            "properties": "nav",
        },
        {"id": "css", "href": "css/book.css", "media_type": "text/css", "properties": ""},
        {
            "id": "font-regular",
            "href": "fonts/Vazirmatn-Regular.woff2",
            "media_type": "font/woff2",
            "properties": "",
        },
        {
            "id": "font-bold",
            "href": "fonts/Vazirmatn-Bold.woff2",
            "media_type": "font/woff2",
            "properties": "",
        },
        {"id": "ofl", "href": "fonts/OFL.txt", "media_type": "text/plain", "properties": ""},
    ]
    spine: list[str] = []
    if assets.cover is not None:
        items.append(
            {
                "id": "cover-image",
                "href": f"images/{assets.cover.name}",
                "media_type": assets.cover.media_type,
                "properties": "cover-image",
            }
        )
        items.append(
            {
                "id": "cover",
                "href": "text/cover.xhtml",
                "media_type": "application/xhtml+xml",
                "properties": "",
            }
        )
        spine.append("cover")
    for index, image in enumerate(assets.images, start=1):
        items.append(
            {
                "id": f"image-{index:03d}",
                "href": f"images/{image.name}",
                "media_type": image.media_type,
                "properties": "",
            }
        )
    for chapter in book.chapters:
        item_id = f"chapter-{chapter.idx:03d}"
        items.append(
            {
                "id": item_id,
                "href": _chapter_file(chapter.idx),
                "media_type": "application/xhtml+xml",
                "properties": "",
            }
        )
        spine.append(item_id)
    if include_incomplete:
        items.append(
            {
                "id": "incomplete",
                "href": "text/incomplete.xhtml",
                "media_type": "application/xhtml+xml",
                "properties": "",
            }
        )
        spine.append("incomplete")
    items.append(
        {
            "id": "colophon",
            "href": "text/colophon.xhtml",
            "media_type": "application/xhtml+xml",
            "properties": "",
        }
    )
    spine.append("colophon")
    return items, spine


def _cover_page(book: ExportBook, cover: ImageAsset) -> str:
    return _render(
        "cover.xhtml.j2",
        image_href=f"../images/{cover.name}",
        alt=book.title_fa or book.title_en,
    )


def _chapters(
    book: ExportBook,
    assets: SourceAssets,
    *,
    link_map: dict[str, str] | None = None,
    toc_idx: int | None = None,
    toc_items: list[tuple[str, str]] | None = None,
) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for chapter in book.chapters:
        result[f"OEBPS/{_chapter_file(chapter.idx)}"] = _render(
            "chapter.xhtml.j2",
            title=chapter_title(chapter),
            body=_chapter_body(
                chapter,
                assets,
                link_map=link_map,
                toc_idx=toc_idx,
                toc_items=toc_items,
            ),
        ).encode("utf-8")
    return result


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo("mimetype", date_time=_ZIP_TIME)
        info.compress_type = zipfile.ZIP_STORED
        info.external_attr = 0o644 << 16
        archive.writestr(info, b"application/epub+zip")
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, entries[name])


def _read_asset(name: str) -> bytes:
    return (_ASSET_DIR / name).read_bytes()


def _accessibility() -> list[dict[str, str]]:
    return [
        {"property": "schema:accessMode", "value": "textual"},
        {"property": "schema:accessModeSufficient", "value": "textual"},
        {"property": "schema:accessibilityFeature", "value": "structuralNavigation"},
        {"property": "schema:accessibilityHazard", "value": "none"},
        {
            "property": "schema:accessibilitySummary",
            "value": "Persian translation with structural navigation and no known hazards.",
        },
        {"property": "dcterms:conformsTo", "value": "https://www.w3.org/TR/epub-a11y-11/"},
    ]


def write_epub(
    book: ExportBook, path: Path, settings: Settings, assets: SourceAssets
) -> Path:
    """Write the EPUB and validate it internally (and with epubcheck when present)."""
    chapters, link_map, toc_idx, toc_items = build_export_structure(book)
    epub_book = replace(book, chapters=chapters) if chapters is not book.chapters else book
    include_incomplete = epub_book.incomplete
    manifest, spine = _manifest(epub_book, assets, include_incomplete=include_incomplete)
    opf = _render(
        "content.opf.j2",
        identifier=_identifier(book),
        title=book.title_fa or book.title_en,
        language="fa",
        author=book.author,
        translator=book.translator,
        source=(
            f"{book.metadata.get('title') or book.title_en}"
            + (
                f" — {book.metadata['identifier']}"
                if book.metadata.get("identifier")
                else ""
            )
        ),
        rights=book.metadata.get("rights") or book.rights_basis,
        publisher=book.metadata.get("publisher"),
        modified=book.generated_at,
        accessibility=_accessibility(),
        manifest=manifest,
        spine=spine,
    )

    nav_items = [
        {"href": _chapter_file(chapter.idx), "title": chapter_title(chapter)}
        for chapter in epub_book.chapters
    ]
    entries: dict[str, bytes] = {
        "META-INF/container.xml": _render(
            "container.xml.j2", opf_path="OEBPS/content.opf"
        ).encode("utf-8"),
        "OEBPS/content.opf": opf.encode("utf-8"),
        "OEBPS/nav.xhtml": _render("nav.xhtml.j2", items=nav_items).encode("utf-8"),
        "OEBPS/css/book.css": _read_asset("book.css"),
        "OEBPS/fonts/Vazirmatn-Regular.woff2": _read_asset("Vazirmatn-Regular.woff2"),
        "OEBPS/fonts/Vazirmatn-Bold.woff2": _read_asset("Vazirmatn-Bold.woff2"),
        "OEBPS/fonts/OFL.txt": _read_asset("OFL.txt"),
    }
    if assets.cover is not None:
        entries["OEBPS/text/cover.xhtml"] = _cover_page(epub_book, assets.cover).encode("utf-8")
        entries[f"OEBPS/images/{assets.cover.name}"] = assets.cover.data
    for image in assets.images:
        entries[f"OEBPS/images/{image.name}"] = image.data
    entries.update(
        _chapters(
            epub_book,
            assets,
            link_map=link_map,
            toc_idx=toc_idx,
            toc_items=toc_items,
        )
    )
    if include_incomplete:
        entries["OEBPS/text/incomplete.xhtml"] = _render("incomplete.xhtml.j2").encode("utf-8")
    entries["OEBPS/text/colophon.xhtml"] = _render(
        "colophon.xhtml.j2",
        project_url=PROJECT_URL,
        rights_basis=book.rights_basis,
        source_format=book.source_format,
        source_sha256=book.source_sha256,
        generated_at=book.generated_at,
    ).encode("utf-8")

    temp = path.with_name(f".{path.stem}.tmp{path.suffix}")
    temp.unlink(missing_ok=True)
    try:
        _write_zip(temp, entries)
        errors = validate_epub(temp)
        if errors:
            raise RuntimeFailure(
                "EPUB validation failed:\n- " + "\n- ".join(errors),
                hint="fix the export inputs or inspect the archive with `scripts/run_epubcheck.sh`",
            )
        _run_epubcheck(temp, settings)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return path


def _resolve_href(opf_path: str, href: str) -> str:
    base = posixpath.dirname(opf_path)
    return posixpath.normpath(posixpath.join(base, href.split("#", 1)[0]))


def _local(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _text(root: etree._Element, name: str) -> str:
    for element in root.xpath(f"//*[local-name()='{name}']"):
        value = "".join(element.itertext()).strip()
        if value:
            return value
    return ""


def _check_xhtml(name: str, data: bytes) -> list[str]:
    errors: list[str] = []
    try:
        root = etree.fromstring(data)
    except etree.XMLSyntaxError as exc:
        return [f"{name}: not well-formed XML ({exc})"]
    if _local(root.tag) != "html":
        errors.append(f"{name}: root element is not <html>")
    if root.get("lang") != "fa":
        errors.append(f"{name}: missing lang=\"fa\"")
    if root.get(_XML_LANG) != "fa":
        errors.append(f"{name}: missing xml:lang=\"fa\"")
    if root.get("dir") != "rtl":
        errors.append(f"{name}: missing dir=\"rtl\"")
    ids: set[str] = set()
    for element in root.xpath("//*[@id]"):
        identifier = str(element.get("id"))
        if identifier in ids:
            errors.append(f"{name}: duplicate id {identifier!r}")
        ids.add(identifier)
    if root.get("id"):
        if str(root.get("id")) in ids:
            errors.append(f"{name}: duplicate id {root.get('id')!r}")
        ids.add(str(root.get("id")))
    for element in root.xpath("//*[@href]"):
        href = str(element.get("href"))
        if href.startswith("#") and href[1:] and href[1:] not in ids:
            errors.append(f"{name}: href {href!r} has no matching id")
    text = data.decode("utf-8", errors="replace")
    if _SCRIPT.search(text):
        errors.append(f"{name}: contains a script element")
    active = _ACTIVE.search(text)
    if active:
        errors.append(f"{name}: contains active content {active.group(0)!r}")
    remote = _REMOTE.search(text)
    if remote:
        errors.append(f"{name}: remote resource reference {remote.group(0)!r}")
    return errors


def validate_epub(path: Path) -> list[str]:
    """Internal structural validation; returns a list of human-readable errors."""
    errors: list[str] = []
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"cannot open EPUB: {exc}"]
    with archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            errors.append("archive contains duplicate entry names")
        if not names or names[0] != "mimetype":
            errors.append("mimetype is not the first ZIP entry")
        if "mimetype" in names:
            info = archive.getinfo("mimetype")
            if info.compress_type != zipfile.ZIP_STORED:
                errors.append("mimetype is compressed (must be ZIP_STORED)")
            if archive.read("mimetype") != b"application/epub+zip":
                errors.append("mimetype content is not application/epub+zip")
        if "META-INF/container.xml" not in names:
            return [*errors, "META-INF/container.xml is missing"]
        try:
            container = etree.fromstring(archive.read("META-INF/container.xml"))
            rootfiles = container.xpath("//*[local-name()='rootfile']/@full-path")
        except etree.XMLSyntaxError as exc:
            return [*errors, f"container.xml is not well-formed: {exc}"]
        if not rootfiles:
            return [*errors, "container.xml has no rootfile"]
        opf_path = str(rootfiles[0])
        if opf_path not in names:
            return [*errors, f"OPF is missing from the archive: {opf_path}"]
        try:
            opf = etree.fromstring(archive.read(opf_path))
        except etree.XMLSyntaxError as exc:
            return [*errors, f"OPF is not well-formed: {exc}"]

        if str(opf.get("version")) != "3.0":
            errors.append("OPF version is not 3.0")
        language = _text(opf, "language")
        if language != "fa":
            errors.append(f"dc:language is {language!r}, expected 'fa'")
        for name in ("identifier", "title", "language"):
            if not _text(opf, name):
                errors.append(f"dc:{name} is missing")
        modified = False
        for meta in opf.xpath("//*[local-name()='meta']"):
            if meta.get("property") == "dcterms:modified":
                modified = bool((meta.text or "").strip())
        if not modified:
            errors.append("dcterms:modified is missing")

        manifest_ids: set[str] = set()
        manifest_items: list[tuple[str, str, str]] = []
        for item in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
            item_id = str(item.get("id", ""))
            href = str(item.get("href", ""))
            media_type = str(item.get("media-type", ""))
            if not item_id or not href:
                errors.append("manifest item is missing id/href")
                continue
            if item_id in manifest_ids:
                errors.append(f"duplicate manifest id {item_id!r}")
            manifest_ids.add(item_id)
            resolved = _resolve_href(opf_path, href)
            if resolved not in names:
                errors.append(f"manifest href does not exist: {resolved}")
            manifest_items.append((href, media_type, resolved))

        spine = opf.xpath("//*[local-name()='spine']")
        if spine:
            if spine[0].get("page-progression-direction") != "rtl":
                errors.append("spine page-progression-direction is not rtl")
            for itemref in spine[0].xpath("*[local-name()='itemref']"):
                idref = str(itemref.get("idref", ""))
                if idref not in manifest_ids:
                    errors.append(f"spine idref {idref!r} is not in the manifest")
        else:
            errors.append("spine is missing")

        for _href, media_type, resolved in manifest_items:
            if media_type != "application/xhtml+xml":
                continue
            errors.extend(_check_xhtml(resolved, archive.read(resolved)))
        if "OEBPS/css/book.css" in names:
            css = archive.read("OEBPS/css/book.css").decode("utf-8", errors="replace")
            if "@import" in css or "url(http" in css.lower():
                errors.append("book.css references a remote stylesheet or resource")
    return errors


def _run_epubcheck(path: Path, settings: Settings) -> None:
    if not settings.export.epub.validate_with_epubcheck:
        return
    executable = shutil.which("epubcheck")
    if executable is None:
        return
    try:
        result = subprocess.run(
            [executable, str(path)],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _LOGGER.warning(
            "epubcheck could not run; keeping the internally validated EPUB",
            extra={"extra_fields": {"error": str(exc)}},
        )
        return
    output = f"{result.stdout}\n{result.stderr}"
    if "ERROR" in output:
        raise RuntimeFailure(
            "epubcheck reported errors:\n" + output.strip(),
            hint="fix the reported EPUB errors; internal validation output is in the message",
        )
    if result.returncode != 0:
        _LOGGER.warning(
            "epubcheck exited non-zero without ERROR findings",
            extra={"extra_fields": {"returncode": result.returncode}},
        )
