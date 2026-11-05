"""Glossary file formats, validation and term normalization (SCHEMAS.md 5.2)."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError
from .ids import sha256_text
from .models import GlossaryKind, GlossaryStatus

_TITLE_PREFIX = re.compile(
    r"^(?:mr|mrs|ms|miss|dr|prof|sir|madam|mister|mistress)\.?\s+", re.IGNORECASE
)
_POSSESSIVE = re.compile(r"['’]s$", re.IGNORECASE)
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)

_KINDS = {item.value for item in GlossaryKind}
_STATUSES = {item.value for item in GlossaryStatus}
_SOURCES = {"candidate", "manual", "imported", "suggested"}
_GENDERS = {"male", "female", "unknown", "not_applicable"}


@dataclass(frozen=True)
class GlossaryEntry:
    term_en: str
    term_fa: str
    kind: str = GlossaryKind.TERM.value
    status: str = GlossaryStatus.PROPOSED.value
    source: str = "candidate"
    confidence: float = 0.0
    aliases_en: tuple[str, ...] = ()
    aliases_fa: tuple[str, ...] = ()
    gender: str = "unknown"
    notes: str = ""
    approved_by: str | None = None


def normalize_term_en(value: str) -> str:
    """Casefold, strip titles/possessives/punctuation; the glossary key."""
    text = _TITLE_PREFIX.sub("", value.strip())
    text = _POSSESSIVE.sub("", text)
    text = _NON_WORD.sub(" ", text.casefold())
    return " ".join(text.split())


def _as_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parts = re.split(r"[|;,]", value)
    elif isinstance(value, (list, tuple)):
        parts = [str(item) for item in value]
    else:
        parts = [str(value)]
    return tuple(part.strip() for part in parts if part.strip())


def _validate_entry(item: dict[str, Any], index: int) -> GlossaryEntry:
    term_en = str(item.get("term_en") or item.get("en") or "").strip()
    term_fa = str(item.get("term_fa") or item.get("fa") or "").strip()
    if not term_en:
        raise ConfigError(f"glossary entry {index} has no English term")
    if not term_fa:
        raise ConfigError(f"glossary entry {index} ({term_en!r}) has no Persian term")
    kind = str(item.get("kind") or GlossaryKind.TERM.value).strip()
    if kind not in _KINDS:
        raise ConfigError(f"glossary entry {index} ({term_en!r}) has unknown kind {kind!r}")
    status = str(item.get("status") or GlossaryStatus.APPROVED.value).strip()
    if status not in _STATUSES:
        raise ConfigError(f"glossary entry {index} ({term_en!r}) has unknown status {status!r}")
    gender = str(item.get("gender") or "unknown").strip()
    if gender not in _GENDERS:
        raise ConfigError(f"glossary entry {index} ({term_en!r}) has unknown gender {gender!r}")
    try:
        confidence = float(item.get("confidence", 1.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"glossary entry {index} ({term_en!r}) has invalid confidence") from exc
    return GlossaryEntry(
        term_en=term_en,
        term_fa=term_fa,
        kind=kind,
        status=status,
        source=str(item.get("source") or "imported"),
        confidence=max(0.0, min(1.0, confidence)),
        aliases_en=_as_list(item.get("aliases_en")),
        aliases_fa=_as_list(item.get("aliases_fa")),
        gender=gender,
        notes=str(item.get("notes") or ""),
        approved_by=item.get("approved_by"),
    )


def load_glossary_file(path: str | Path) -> list[GlossaryEntry]:
    """Parse a YAML (``entries`` list) or CSV (``en,fa[,kind,...]``) glossary."""
    file = Path(path)
    if not file.is_file():
        raise ConfigError(
            f"glossary file not found: {file}",
            hint="pass a YAML/CSV file with at least en and fa columns",
        )
    suffix = file.suffix.lower()
    if suffix == ".csv":
        text = file.read_text(encoding="utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None:
            raise ConfigError(f"glossary CSV {file} has no header")
        parsed = [
            _validate_entry(dict(row), index) for index, row in enumerate(reader, start=1)
        ]
        if not parsed:
            raise ConfigError(f"glossary CSV {file} contains no entries")
        return parsed
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid glossary YAML in {file}: {exc}") from exc
    if isinstance(data, list):
        entries = data
    elif isinstance(data, dict) and isinstance(data.get("entries"), list):
        entries = data["entries"]
    else:
        raise ConfigError(f"glossary YAML {file} must contain an 'entries' list")
    parsed = [
        _validate_entry(item, index)
        for index, item in enumerate(entries, start=1)
        if isinstance(item, dict)
    ]
    if not parsed:
        raise ConfigError(f"glossary YAML {file} contains no valid entries")
    return parsed


def entry_to_dict(entry: GlossaryEntry) -> dict[str, Any]:
    return {
        "term_en": entry.term_en,
        "term_fa": entry.term_fa,
        "kind": entry.kind,
        "status": entry.status,
        "confidence": entry.confidence,
        "aliases_en": list(entry.aliases_en),
        "aliases_fa": list(entry.aliases_fa),
        "gender": entry.gender,
        "notes": entry.notes,
    }


def write_glossary_yaml(path: str | Path, *, book_slug: str, entries: list[GlossaryEntry]) -> Path:
    target = Path(path)
    payload = {
        "version": 1,
        "book_slug": book_slug,
        "entries": [entry_to_dict(entry) for entry in entries],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return target


def glossary_hash(entries: list[GlossaryEntry]) -> str:
    payload = json.dumps([entry_to_dict(entry) for entry in entries], sort_keys=True, ensure_ascii=False)
    return f"sha256:{sha256_text(payload)}"
