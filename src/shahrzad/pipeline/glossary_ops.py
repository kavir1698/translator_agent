"""Glossary persistence: import, export, sync and the gate-1b blockers."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from shahrzad.core.config import Settings
from shahrzad.core.db import Database
from shahrzad.core.errors import ConfigError
from shahrzad.core.glossary import (
    GlossaryEntry,
    load_glossary_file,
    normalize_term_en,
    write_glossary_yaml,
)
from shahrzad.core.models import GlossaryKind
from shahrzad.persian.lint import GlossaryTerm


def resolve_glossary_path(*, value: str | None, root: Path, base: Path) -> Path:
    if not value:
        raise ConfigError(
            "glossary.mode=provided requires glossary.path",
            hint="pass --glossary terms.yaml to `shz init`, or set glossary.path",
        )
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    for candidate in (root / path, base / path):
        if candidate.is_file():
            return candidate
    raise ConfigError(f"glossary file not found: {path}")


def import_entries(
    db: Database,
    *,
    book_id: int,
    entries: Sequence[GlossaryEntry],
    force_status: str | None = None,
    source: str | None = None,
    approved_by: str | None = None,
    preserve_approved: bool = True,
) -> int:
    count = 0
    for entry in entries:
        db.upsert_glossary_entry(
            book_id=book_id,
            term_en=entry.term_en,
            term_fa=entry.term_fa,
            kind=entry.kind,
            status=force_status or entry.status,
            source=source or entry.source,
            confidence=entry.confidence,
            aliases_en=entry.aliases_en,
            aliases_fa=entry.aliases_fa,
            gender=entry.gender,
            notes=entry.notes,
            approved_by=approved_by if approved_by is not None else entry.approved_by,
            preserve_approved=preserve_approved,
        )
        count += 1
    return count


def import_provided_glossary(
    db: Database, *, book_id: int, settings: Settings, root: Path, base: Path
) -> int:
    path = resolve_glossary_path(value=settings.glossary.path, root=root, base=base)
    entries = load_glossary_file(path)
    return import_entries(
        db,
        book_id=book_id,
        entries=entries,
        force_status="approved",
        source="imported",
        approved_by="imported",
        preserve_approved=False,
    )


def _rows_to_entries(rows: Sequence[Any]) -> list[GlossaryEntry]:
    entries: list[GlossaryEntry] = []
    for row in rows:
        entries.append(
            GlossaryEntry(
                term_en=str(row["term_en"]),
                term_fa=str(row["term_fa"] or ""),
                kind=str(row["kind"]),
                status=str(row["status"]),
                source=str(row["source"]),
                confidence=float(row["confidence"] or 0.0),
                aliases_en=tuple(json.loads(row["aliases_json"] or "[]")),
                aliases_fa=tuple(json.loads(row["aliases_fa_json"] or "[]")),
                gender=str(row["gender"]),
                notes=str(row["notes"] or ""),
                approved_by=row["approved_by"],
            )
        )
    return entries


def write_glossary_artifact(
    db: Database, *, book_id: int, base: Path, book_slug: str
) -> Path:
    rows = db.glossary_entries(book_id)
    return write_glossary_yaml(
        base / "artifacts" / "glossary.yaml",
        book_slug=book_slug,
        entries=_rows_to_entries(rows),
    )


def sync_glossary_artifact(db: Database, *, book_id: int, base: Path, book_slug: str) -> int:
    path = base / "artifacts" / "glossary.yaml"
    if not path.is_file():
        raise ConfigError(
            f"no glossary artifact at {path}",
            hint="run `shz glossary export` or `shz analyze --auto-glossary` first",
        )
    entries = load_glossary_file(path)
    file_keys = {(normalize_term_en(entry.term_en), entry.kind) for entry in entries}
    existing = db.glossary_entries(book_id)
    count = import_entries(
        db,
        book_id=book_id,
        entries=entries,
        source="manual",
        preserve_approved=False,
    )
    for row in existing:
        key = (normalize_term_en(str(row["term_en"])), str(row["kind"]))
        if key not in file_keys and row["status"] != "deprecated":
            db.set_glossary_status(int(row["id"]), "deprecated")
    return count


def approved_terms(db: Database, book_id: int) -> list[GlossaryTerm]:
    terms: list[GlossaryTerm] = []
    for row in db.glossary_entries(book_id, status="approved"):
        if not row["term_fa"]:
            continue
        terms.append(
            GlossaryTerm(
                en=str(row["term_en"]),
                fa=str(row["term_fa"]),
                kind=str(row["kind"]),
                aliases_en=tuple(json.loads(row["aliases_json"] or "[]")),
                aliases_fa=tuple(json.loads(row["aliases_fa_json"] or "[]")),
                status="approved",
            )
        )
    return terms


def gate_blockers(db: Database, book_id: int) -> list[str]:
    """Characters/places that still need approval before translation (gate 1b)."""
    blockers: list[str] = []
    for row in db.glossary_entries(book_id):
        if row["kind"] not in {GlossaryKind.CHARACTER, GlossaryKind.PLACE}:
            continue
        if row["status"] == "proposed":
            blockers.append(f"{row['term_en']} ({row['kind']}, {row['status']})")
    return blockers
