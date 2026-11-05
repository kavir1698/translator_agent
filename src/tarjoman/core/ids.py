"""Deterministic identifiers. Ids never derive from content."""

from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = _SLUG_STRIP.sub("-", normalized.lower()).strip("-")
    return slug or "book"


def chapter_id(chapter_idx: int) -> str:
    """c003"""
    return f"c{chapter_idx:03d}"


def segment_id(chapter_idx: int, segment_idx: int) -> str:
    """c003.s0042"""
    return f"{chapter_id(chapter_idx)}.s{segment_idx:04d}"


def window_id(chapter_idx: int, window_idx: int) -> str:
    """c003.w004"""
    return f"{chapter_id(chapter_idx)}.w{window_idx:03d}"


def run_id(now: datetime | None = None) -> str:
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S")
    return f"run_{stamp}_{secrets.token_hex(3)}"


def utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def short_hash(*parts: object) -> str:
    joined = "\x1f".join(str(part) for part in parts)
    return sha256_text(joined)[:16]
