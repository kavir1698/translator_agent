"""Rebuild the EPUB reading order from the source book's own TOC.

The chapterizer splits at every heading, which is useful for TXT/DOCX but
wrong for EPUBs whose stories are split across several spine documents.  The
source nav/NCX is the better structure: group each TOC entry with the
continuation documents that follow it, use its label, and put review essays
("Thoughts on X") after the story they discuss.

This runs at export time only, so existing translations and segment ids are
left untouched.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from lxml import etree

from shahrzad.core.logging import get_logger

from .models import ExportBook, ExportChapter
from .xhtml import chapter_title

_LOGGER = get_logger("export.structure")

_EPUB_NS = "http://www.idpf.org/2007/ops"
_GENERIC_TITLE = re.compile(r"chapter\s+\d+", re.IGNORECASE)
_DOC_ID = re.compile(r"doc(\d+)")


@dataclass(frozen=True)
class TocEntry:
    title: str
    href: str
    position: int = 0


@dataclass
class _Group:
    entry: TocEntry
    position: int
    chapters: list[ExportChapter]


def _epub_type(element: etree._Element) -> str:
    for key, value in element.attrib.items():
        raw = str(key)
        if raw.endswith("}type") or raw == "epub:type":
            return str(value)
    return ""


def _resolve(base_dir: str, href: str) -> str:
    value = href.split("#", 1)[0]
    return posixpath.normpath(posixpath.join(base_dir, value))


def _package(path: Any) -> tuple[str, str, dict[str, dict[str, str]], list[str]] | None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "META-INF/container.xml" not in names:
                return None
            container = etree.fromstring(archive.read("META-INF/container.xml"))
            rootfiles = container.xpath("//*[local-name()='rootfile']/@full-path")
            if not rootfiles:
                return None
            opf_path = str(rootfiles[0])
            opf = etree.fromstring(archive.read(opf_path))
    except (OSError, KeyError, zipfile.BadZipFile, etree.XMLSyntaxError):
        return None
    opf_dir = posixpath.dirname(opf_path)
    manifest: dict[str, dict[str, str]] = {}
    for item in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
        item_id = str(item.get("id", ""))
        manifest[item_id] = {
            "href": _resolve(opf_dir, str(item.get("href", ""))),
            "media_type": str(item.get("media-type", "")),
            "properties": str(item.get("properties", "")),
        }
    linear: list[str] = []
    nonlinear: list[str] = []
    for itemref in opf.xpath("//*[local-name()='spine']/*[local-name()='itemref']"):
        ref = str(itemref.get("idref", ""))
        if ref not in manifest:
            continue
        target = linear if itemref.get("linear", "yes").lower() != "no" else nonlinear
        target.append(manifest[ref]["href"])
    return opf_path, opf_dir, manifest, [*linear, *nonlinear]


def read_spine_hrefs(path: Any) -> list[str]:
    package = _package(path)
    if package is None:
        return []
    _, _, _, spine = package
    return spine


def read_epub_toc(path: Any) -> list[TocEntry]:
    try:
        with zipfile.ZipFile(path) as archive:
            package = _package(path)
            if package is None:
                return []
            opf_path, _, manifest, _ = package
            del opf_path
            nav_item = next(
                (
                    item
                    for item in manifest.values()
                    if "nav" in item["properties"].split()
                    and item["media_type"] == "application/xhtml+xml"
                ),
                None,
            )
            if nav_item is not None and nav_item["href"] in archive.namelist():
                root = etree.fromstring(archive.read(nav_item["href"]))
                base = posixpath.dirname(nav_item["href"])
                navs = root.xpath("//*[local-name()='nav']")
                nav = next(
                    (item for item in navs if "toc" in _epub_type(item).split()),
                    navs[0] if navs else None,
                )
                if nav is not None:
                    entries: list[TocEntry] = []
                    for anchor in nav.xpath(".//*[local-name()='a']"):
                        href = anchor.get("href")
                        title = " ".join("".join(anchor.itertext()).split())
                        if href and title:
                            entries.append(TocEntry(title=title, href=_resolve(base, href)))
                    if entries:
                        return entries
            ncx_item = next(
                (
                    item
                    for item in manifest.values()
                    if item["media_type"] == "application/x-dtbncx+xml"
                ),
                None,
            )
            if ncx_item is not None and ncx_item["href"] in archive.namelist():
                root = etree.fromstring(archive.read(ncx_item["href"]))
                base = posixpath.dirname(ncx_item["href"])
                entries = []
                for point in root.xpath("//*[local-name()='navPoint']"):
                    labels = point.xpath(
                        "./*[local-name()='navLabel']//*[local-name()='text']/text()"
                    )
                    sources = point.xpath("./*[local-name()='content']/@src")
                    title = " ".join(" ".join(labels).split())
                    if sources and title:
                        entries.append(TocEntry(title=title, href=_resolve(base, str(sources[0]))))
                return entries
    except (OSError, KeyError, zipfile.BadZipFile, etree.XMLSyntaxError):
        return []
    return []


def _doc_position(chapter: ExportChapter) -> int:
    match = _DOC_ID.fullmatch(chapter.source_doc or "")
    return int(match.group(1)) if match is not None else 0


def _normalize_title(value: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", value.casefold()).split())


def _read_moves(book: ExportBook) -> list[tuple[str, str]]:
    """Read optional per-book TOC order corrections from artifacts/toc_order.yaml."""
    path = Path(book.source_path).parent.parent / "artifacts" / "toc_order.yaml"
    if not path.is_file():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        _LOGGER.warning("ignoring %s: %s", path, exc)
        return []
    raw = data.get("moves") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    moves: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, dict) and item.get("item") and item.get("after"):
            moves.append((str(item["item"]), str(item["after"])))
    return moves


def _find_group(groups: list[_Group], title: str) -> _Group | None:
    needle = _normalize_title(title)
    if not needle:
        return None
    exact = [
        group for group in groups if _normalize_title(group.entry.title) == needle
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [
        group for group in groups if needle in _normalize_title(group.entry.title)
    ]
    if len(partial) == 1:
        return partial[0]
    _LOGGER.warning("toc_order: %r matched %d chapters", title, len(partial))
    return None


def _apply_moves(groups: list[_Group], moves: list[tuple[str, str]]) -> None:
    for item, after in moves:
        source = _find_group(groups, item)
        target = _find_group(groups, after)
        if source is None or target is None or source is target:
            continue
        groups.remove(source)
        groups.insert(groups.index(target) + 1, source)


def _group_title(group: _Group) -> tuple[str, str | None]:
    first = group.chapters[0]
    generic = first.title_en is None or _GENERIC_TITLE.fullmatch(first.title_en.strip())
    if generic or first.title_en is None:
        return group.entry.title, None
    return first.title_en, first.title_fa


def _build_chapter(group: _Group) -> ExportChapter:
    first = group.chapters[0]
    title_en, title_fa = _group_title(group)
    return ExportChapter(
        idx=first.idx,
        kind=first.kind,
        title_en=title_en,
        title_fa=title_fa,
        segments=[segment for chapter in group.chapters for segment in chapter.segments],
        source_doc=first.source_doc,
    )


def _basename(href: str) -> str:
    return PurePosixPath(href).name


def _entry_positions(toc: list[TocEntry], spine: list[str]) -> list[TocEntry]:
    by_href = {href: index + 1 for index, href in enumerate(spine)}
    by_name = {_basename(href): index + 1 for index, href in enumerate(spine)}
    resolved: list[TocEntry] = []
    for entry in toc:
        position = by_href.get(entry.href) or by_name.get(_basename(entry.href), 0)
        if position:
            resolved.append(replace(entry, position=position))
    return sorted(resolved, key=lambda item: item.position)


def build_export_structure(
    book: ExportBook,
) -> tuple[list[ExportChapter], dict[str, str], int | None, list[tuple[str, str]]]:
    """Return (chapters, source-href map, TOC page idx, generated TOC items)."""
    toc = read_epub_toc(book.source_path)
    spine = read_spine_hrefs(book.source_path)
    if not toc or not spine:
        return book.chapters, {}, None, []

    entries = _entry_positions(toc, spine)
    if not entries:
        return book.chapters, {}, None, []

    chapters = sorted(book.chapters, key=lambda item: item.idx)
    groups: list[_Group] = []
    for index, entry in enumerate(entries):
        next_position = entries[index + 1].position if index + 1 < len(entries) else 10**9
        members = [
            chapter
            for chapter in chapters
            if entry.position <= _doc_position(chapter) < next_position
        ]
        if members:
            groups.append(_Group(entry=entry, position=entry.position, chapters=members))
    if not groups:
        return book.chapters, {}, None, []

    _apply_moves(groups, _read_moves(book))

    kept = [
        group for group in groups if any(chapter.segments for chapter in group.chapters)
    ]
    exported = [_build_chapter(group) for group in kept]
    if not exported:
        return book.chapters, {}, None, []
    exported = [
        replace(chapter, idx=position)
        for position, chapter in enumerate(exported, start=1)
    ]
    target_by_group = {
        id(group): chapter.idx
        for group, chapter in zip(kept, exported, strict=True)
    }

    toc_index = next(
        (
            chapter.idx
            for chapter in exported
            if (chapter.title_en or "").strip().casefold() == "contents"
            or (chapter.title_fa or "").strip() == "فهرست"
        ),
        None,
    )

    link_map: dict[str, str] = {}
    for group, chapter in zip(kept, exported, strict=True):
        target = f"chapter-{chapter.idx:03d}.xhtml"
        for member in group.chapters:
            position = _doc_position(member)
            if 0 < position <= len(spine):
                link_map[_basename(spine[position - 1])] = target
        if group.entry.href:
            link_map.setdefault(_basename(group.entry.href), target)
    for entry in entries:
        name = _basename(entry.href)
        if name in link_map:
            continue
        target_group = next(
            (group for group in kept if group.position >= entry.position),
            kept[-1],
        )
        link_map[name] = f"chapter-{target_by_group[id(target_group)]:03d}.xhtml"

    toc_items: list[tuple[str, str]] = []
    if toc_index is not None:
        toc_items = [
            (chapter_title(chapter), f"chapter-{chapter.idx:03d}.xhtml")
            for chapter in exported
            if chapter.idx != toc_index
        ]
    return exported, link_map, toc_index, toc_items
