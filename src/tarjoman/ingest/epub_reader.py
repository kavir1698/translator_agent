"""EPUB reader: zipfile + container.xml + OPF + spine, lxml HTML (no ebooklib)."""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from lxml import etree
from lxml import html as lxml_html

from tarjoman.core.errors import RuntimeFailure

from .blocks import BlockKind, epub_type, iter_blocks
from .types import Document, IngestResult

MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_RATIO = 200


def _safe_read(archive: zipfile.ZipFile, name: str, budget: dict[str, int]) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_ENTRY_BYTES:
        raise RuntimeFailure(f"EPUB entry {name} is too large ({info.file_size} bytes)")
    if (
        info.compress_size > 0
        and info.file_size > 10 * 1024 * 1024
        and info.file_size / info.compress_size > MAX_RATIO
    ):
        raise RuntimeFailure(f"EPUB entry {name} has a suspicious compression ratio")
    budget["total"] += info.file_size
    if budget["total"] > MAX_TOTAL_BYTES:
        raise RuntimeFailure("EPUB expands beyond the 512 MB ingest limit")
    return archive.read(name)


def _parse_html(raw: bytes) -> etree._Element:
    """Parse one XHTML document, preferring UTF-8 when the bytes are valid.

    lxml's HTML parser guesses Latin-1 when non-ASCII bytes appear before the
    document's charset declaration (for example in ``<title>``), which mangles
    otherwise valid UTF-8 text.
    """
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return lxml_html.document_fromstring(raw)
    return lxml_html.document_fromstring(raw, parser=lxml_html.HTMLParser(encoding="utf-8"))


def _local(element: etree._Element) -> str:
    tag = str(element.tag)
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _first_text(root: etree._Element, name: str) -> str | None:
    for element in root.xpath(f"//*[local-name()='{name}']"):
        text = "".join(element.itertext()).strip()
        if text:
            return text
    return None


def _resolve(base_dir: str, href: str) -> str:
    joined = PurePosixPath(base_dir) / href if base_dir else PurePosixPath(href)
    parts: list[str] = []
    for part in joined.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part not in (".", ""):
            parts.append(part)
    return "/".join(parts)


def read_epub(path: str | Path) -> IngestResult:
    file = Path(path)
    with zipfile.ZipFile(file) as archive:
        names = set(archive.namelist())
        budget = {"total": 0}
        if "META-INF/container.xml" not in names:
            raise RuntimeFailure(f"{file} has no META-INF/container.xml")
        container = etree.fromstring(_safe_read(archive, "META-INF/container.xml", budget))
        rootfiles = container.xpath("//*[local-name()='rootfile']/@full-path")
        if not rootfiles:
            raise RuntimeFailure(f"{file} container.xml has no rootfile")
        opf_path = str(rootfiles[0])
        opf = etree.fromstring(_safe_read(archive, opf_path, budget))
        opf_dir = str(PurePosixPath(opf_path).parent)

        manifest: dict[str, dict[str, str]] = {}
        for item in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
            item_id = item.get("id", "")
            manifest[item_id] = {
                "href": _resolve(opf_dir, item.get("href", "")),
                "media_type": item.get("media-type", ""),
                "properties": item.get("properties", ""),
            }

        spine_items: list[tuple[str, bool]] = []
        for itemref in opf.xpath("//*[local-name()='spine']/*[local-name()='itemref']"):
            ref = itemref.get("idref", "")
            if ref in manifest:
                spine_items.append(
                    (manifest[ref]["href"], itemref.get("linear", "yes").lower() != "no")
                )
        # Non-linear documents (commonly endnotes) follow the linear body so their
        # anchors and footnote segments still exist in the workspace.
        spine_items = [item for item in spine_items if item[1]] + [
            item for item in spine_items if not item[1]
        ]

        metadata = {
            "title": _first_text(opf, "title") or file.stem,
            "author": _first_text(opf, "creator"),
            "language": _first_text(opf, "language"),
            "publisher": _first_text(opf, "publisher"),
            "rights": _first_text(opf, "rights"),
            "identifier": _first_text(opf, "identifier"),
        }
        cover = None
        for item in manifest.values():
            if "cover-image" in item["properties"].split():
                cover = item["href"]
                break
        if cover is None:
            for meta in opf.xpath("//*[local-name()='meta']"):
                if meta.get("name") == "cover" and meta.get("content") in manifest:
                    cover = manifest[str(meta.get("content"))]["href"]
                    break

        documents: list[Document] = []
        skipped: list[dict[str, str]] = []
        for index, (href, linear) in enumerate(spine_items, start=1):
            if href not in names:
                skipped.append({"href": href, "reason": "missing from archive"})
                continue
            raw = _safe_read(archive, href, budget)
            try:
                tree = _parse_html(raw)
            except (etree.ParserError, ValueError) as exc:
                skipped.append({"href": href, "reason": str(exc)})
                continue
            body = tree.find(".//body")
            if body is None:
                body = tree
            blocks = iter_blocks(body)
            if not blocks:
                skipped.append({"href": href, "reason": "no translatable blocks"})
                continue
            title = next(
                (block.text for block in blocks if block.kind is BlockKind.HEADING), None
            )
            doc_type = epub_type(body)
            if not linear and not doc_type:
                doc_type = "backmatter"
            documents.append(
                Document(
                    id=f"doc{index:03d}",
                    blocks=blocks,
                    title=title,
                    epub_type=doc_type,
                    href=href,
                )
            )

        if not documents:
            raise RuntimeFailure(f"{file} has no readable spine documents")

    return IngestResult(
        source_format="epub",
        documents=documents,
        title=metadata["title"] or file.stem,
        author=metadata["author"],
        language=metadata["language"],
        publisher=metadata["publisher"],
        rights=metadata["rights"],
        identifier=metadata["identifier"],
        cover=cover,
        metadata={"skipped_documents": skipped} if skipped else {},
    )
