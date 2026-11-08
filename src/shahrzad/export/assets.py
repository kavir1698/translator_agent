"""Extract images from the original EPUB/DOCX without modifying the source."""

from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from lxml import etree

from shahrzad.core.errors import RuntimeFailure
from shahrzad.core.ids import sha256_bytes

from .models import ExportBook

MAX_ENTRY_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_RATIO = 200
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

_CONTAINER = "META-INF/container.xml"


@dataclass(frozen=True)
class ImageAsset:
    name: str
    media_type: str
    data: bytes


@dataclass
class SourceAssets:
    cover: ImageAsset | None = None
    images: list[ImageAsset] = field(default_factory=list)
    by_source: dict[str, ImageAsset] = field(default_factory=dict)


def _clean(path: str) -> str:
    value = path.replace("\\", "/").strip()
    value = posixpath.normpath(value)
    while value.startswith("../"):
        value = value[3:]
    return value[2:] if value.startswith("./") else value.lstrip("/")


def _media_type(name: str) -> str | None:
    return _IMAGE_TYPES.get(Path(name).suffix.lower())


def _entry_candidates(name: str) -> list[str]:
    decoded = unquote(name)
    result = [name, decoded, _clean(name), _clean(decoded)]
    for value in list(result):
        base = posixpath.basename(value)
        if base and base not in result:
            result.append(base)
    return result


def _index(assets: SourceAssets, source: str, asset: ImageAsset) -> None:
    for candidate in _entry_candidates(source):
        assets.by_source.setdefault(candidate, asset)


def _normalize_entry_name(name: str) -> str:
    return _clean(unquote(name))


def _safe_read(
    archive: zipfile.ZipFile, name: str, budget: dict[str, int]
) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_ENTRY_BYTES:
        raise RuntimeFailure(
            f"source archive entry {name} is too large ({info.file_size} bytes)",
            hint="use a smaller/safer source file",
        )
    if (
        info.compress_size > 0
        and info.file_size > 10 * 1024 * 1024
        and info.file_size / info.compress_size > MAX_RATIO
    ):
        raise RuntimeFailure(
            f"source archive entry {name} has a suspicious compression ratio",
            hint="use a source file that was not crafted to expand excessively",
        )
    budget["total"] += info.file_size
    if budget["total"] > MAX_TOTAL_BYTES:
        raise RuntimeFailure(
            "source archive expands beyond the 512 MB export limit",
            hint="use a smaller source file",
        )
    return archive.read(name)


def _epub_cover_hint(
    archive: zipfile.ZipFile, budget: dict[str, int]
) -> str | None:
    try:
        container = etree.fromstring(_safe_read(archive, _CONTAINER, budget))
        rootfiles = container.xpath("//*[local-name()='rootfile']/@full-path")
        if not rootfiles:
            return None
        opf_path = str(rootfiles[0])
        opf = etree.fromstring(_safe_read(archive, opf_path, budget))
    except (KeyError, OSError, etree.XMLSyntaxError):
        return None
    opf_dir = posixpath.dirname(opf_path)
    manifest: dict[str, tuple[str, str]] = {}
    for item in opf.xpath("//*[local-name()='manifest']/*[local-name()='item']"):
        item_id = str(item.get("id", ""))
        href = str(item.get("href", ""))
        properties = str(item.get("properties", ""))
        manifest[item_id] = (posixpath.normpath(posixpath.join(opf_dir, href)), properties)
    for path, properties in manifest.values():
        if "cover-image" in properties.split():
            return path
    for meta in opf.xpath("//*[local-name()='meta']"):
        if meta.get("name") == "cover" and str(meta.get("content", "")) in manifest:
            return manifest[str(meta.get("content"))][0]
    return None


def _resolve_cover(
    candidates: list[str | None], names: list[str]
) -> str | None:
    by_clean = {_normalize_entry_name(name): name for name in names}
    by_base = {posixpath.basename(_normalize_entry_name(name)): name for name in names}
    for candidate in candidates:
        if not candidate:
            continue
        cleaned = _normalize_entry_name(candidate)
        if cleaned in by_clean:
            return by_clean[cleaned]
        base = posixpath.basename(cleaned)
        if base in by_base:
            return by_base[base]
    return None


def _zip_images(
    archive: zipfile.ZipFile, prefix: str, budget: dict[str, int]
) -> dict[str, tuple[bytes, str]]:
    result: dict[str, tuple[bytes, str]] = {}
    for name in sorted(archive.namelist()):
        if prefix and not name.startswith(prefix):
            continue
        media_type = _media_type(name)
        if media_type is None:
            continue
        try:
            result[name] = (_safe_read(archive, name, budget), media_type)
        except (KeyError, OSError, zipfile.BadZipFile):
            continue
    return result


def _build_assets(
    raw: dict[str, tuple[bytes, str]], cover_entry: str | None
) -> SourceAssets:
    assets = SourceAssets()
    cover: ImageAsset | None = None
    hashes: dict[str, ImageAsset] = {}

    if cover_entry is not None and cover_entry in raw:
        data, media_type = raw[cover_entry]
        extension = Path(cover_entry).suffix.lower()
        cover = ImageAsset(name=f"cover{extension}", media_type=media_type, data=data)
        hashes[sha256_bytes(data)] = cover
        _index(assets, cover_entry, cover)

    for name in sorted(raw):
        data, media_type = raw[name]
        digest = sha256_bytes(data)
        asset = hashes.get(digest)
        if asset is None:
            extension = Path(name).suffix.lower()
            asset = ImageAsset(
                name=f"img-{digest[:10]}{extension}", media_type=media_type, data=data
            )
            hashes[digest] = asset
        _index(assets, name, asset)

    assets.cover = cover
    assets.images = [asset for digest, asset in hashes.items() if asset is not cover]
    return assets


def load_source_assets(book: ExportBook) -> SourceAssets:
    """Read images from the original file. A missing or broken source yields no assets."""
    path = book.source_path
    if not path.is_file():
        return SourceAssets()
    budget = {"total": 0}
    try:
        with zipfile.ZipFile(path) as archive:
            if book.source_format == "docx":
                raw = _zip_images(archive, "word/media/", budget)
                cover_entry = _resolve_cover([book.cover], sorted(raw))
            else:
                hint = _epub_cover_hint(archive, budget)
                raw = _zip_images(archive, "", budget)
                cover_entry = _resolve_cover(
                    [book.cover, book.metadata.get("cover"), hint],
                    sorted(raw),
                )
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError):
        return SourceAssets()
    return _build_assets(raw, cover_entry)


def find_asset(assets: SourceAssets, src: str) -> ImageAsset | None:
    """Resolve an author-supplied src against extracted assets, tolerantly."""
    if not src:
        return None
    raw = src.replace("\\", "/")
    cleaned = _clean(raw)
    decoded = _clean(unquote(raw))
    candidates = [raw, decoded, cleaned, posixpath.basename(cleaned)]
    for candidate in list(candidates):
        base = posixpath.basename(candidate)
        if base and base not in candidates:
            candidates.append(base)
    for candidate in candidates:
        asset = assets.by_source.get(candidate)
        if asset is not None:
            return asset
    return None
