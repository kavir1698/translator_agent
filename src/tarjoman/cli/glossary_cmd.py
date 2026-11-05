"""`tj glossary` — Gate 1b review and the three glossary modes (plan 6.1.5-6.2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.table import Table

from tarjoman.core.db import Database
from tarjoman.core.errors import ConfigError, RuntimeFailure
from tarjoman.core.glossary import (
    GlossaryEntry,
    load_glossary_file,
    normalize_term_en,
    write_glossary_yaml,
)
from tarjoman.core.models import GlossaryKind, GlossaryStatus
from tarjoman.core.paths import find_book_dir
from tarjoman.pipeline.glossary_ops import (
    gate_blockers,
    import_entries,
    sync_glossary_artifact,
    write_glossary_artifact,
)

from .common import Context, load_context
from .ui import console, handle_errors

glossary_app = typer.Typer(help="Inspect and edit the approved glossary.")
_KINDS = {item.value for item in GlossaryKind}
_STATUSES = {item.value for item in GlossaryStatus}


def _workspace(context: Context) -> tuple[Path, Database, Any]:
    base = find_book_dir(context.root, context.slug, context.settings.project.books_dir)
    db = Database(base / "book.db")
    db.initialize()
    book = db.fetchone("SELECT * FROM books LIMIT 1")
    if book is None:
        db.close()
        raise ConfigError("the workspace has no book row", hint="re-run `tj init`")
    return base, db, book


def _candidates(base: Path) -> dict[str, dict[str, Any]]:
    path = base / "artifacts" / "glossary.candidates.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in data["candidates"]:
        if isinstance(item, dict) and item.get("en"):
            result[normalize_term_en(str(item["en"]))] = item
    return result


def _entry(row: Any, candidate: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "id": int(row["id"]),
        "term_en": str(row["term_en"]),
        "term_fa": str(row["term_fa"] or ""),
        "kind": str(row["kind"]),
        "status": str(row["status"]),
        "confidence": float(row["confidence"] or 0.0),
        "aliases_en": json.loads(row["aliases_json"] or "[]"),
        "aliases_fa": json.loads(row["aliases_fa_json"] or "[]"),
        "gender": str(row["gender"]),
        "notes": str(row["notes"] or ""),
    }
    if candidate is not None:
        payload["count"] = int(candidate.get("count") or 0)
        payload["alternatives"] = candidate.get("alternatives") or []
        payload["rationale"] = str(candidate.get("rationale") or "")
    return payload


def _show(
    rows: list[Any],
    *,
    json_out: bool,
    title: str,
    candidates: dict[str, dict[str, Any]] | None = None,
) -> None:
    if json_out:
        typer.echo(
            json.dumps(
                [
                    _entry(
                        row,
                        (candidates or {}).get(normalize_term_en(str(row["term_en"]))),
                    )
                    for row in rows
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    if not rows:
        console.print("[yellow]no glossary entries[/yellow]")
        return
    table = Table(title=title, show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("en", overflow="fold")
    table.add_column("fa", overflow="fold")
    table.add_column("kind")
    table.add_column("status")
    table.add_column("count", justify="right")
    table.add_column("conf", justify="right")
    table.add_column("alternatives", overflow="fold")
    for row in rows:
        candidate = (candidates or {}).get(normalize_term_en(str(row["term_en"])))
        table.add_row(
            str(row["id"]),
            str(row["term_en"]),
            str(row["term_fa"] or ""),
            str(row["kind"]),
            str(row["status"]),
            str(int(candidate.get("count") or 0)) if candidate else "",
            f"{float(row['confidence'] or 0):.2f}",
            " | ".join(str(item) for item in (candidate or {}).get("alternatives", [])),
        )
    console.print(table)


def _aliases(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.replace(";", "|").split("|") if part.strip())


def _refresh(db: Database, base: Path, book: Any) -> None:
    write_glossary_artifact(db, book_id=int(book["id"]), base=base, book_slug=str(book["slug"]))


@glossary_app.command("list")
@handle_errors
def list_entries(
    ctx: typer.Context,
    status: str | None = typer.Option(None, "--status", help="proposed|approved|rejected|deprecated"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """List glossary entries."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    base, db, book = _workspace(context)
    try:
        if status is not None and status not in _STATUSES:
            raise ConfigError(f"unknown glossary status: {status}")
        _show(
            db.glossary_entries(int(book["id"]), status=status),
            json_out=json_out,
            title="Glossary",
            candidates=_candidates(base),
        )
    finally:
        db.close()


@glossary_app.command("review")
@handle_errors
def review(
    ctx: typer.Context,
    min_confidence: float = typer.Option(0.0, "--min-confidence"),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Show proposed entries and the gate-1b blockers."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    base, db, book = _workspace(context)
    try:
        rows = [
            row
            for row in db.glossary_entries(int(book["id"]), status="proposed")
            if float(row["confidence"] or 0) >= min_confidence
        ]
        _show(
            rows,
            json_out=json_out,
            title="Proposed glossary entries",
            candidates=_candidates(base),
        )
        blockers = gate_blockers(db, int(book["id"]))
        if blockers and not json_out:
            console.print(
                f"[yellow]{len(blockers)} character/place entries still need approval "
                f"before translation:[/yellow] {', '.join(blockers[:8])}"
            )
    finally:
        db.close()


def _approve_rows(
    db: Database, *, book_id: int, rows: list[Any], approved_by: str
) -> int:
    count = 0
    for row in rows:
        if str(row["status"]) == "approved":
            continue
        db.set_glossary_status(int(row["id"]), "approved", approved_by=approved_by)
        count += 1
    return count


@glossary_app.command("approve")
@handle_errors
def approve(
    ctx: typer.Context,
    ids: list[int] = typer.Argument(None),
    min_confidence: float | None = typer.Option(None, "--min-confidence"),
) -> None:
    """Approve entries by id, or every proposed entry above a confidence."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    base, db, book = _workspace(context)
    try:
        book_id = int(book["id"])
        rows: list[Any] = []
        for entry_id in ids or []:
            row = db.get_glossary_entry(entry_id)
            if row is None:
                raise RuntimeFailure(f"glossary entry {entry_id} does not exist")
            rows.append(row)
        if min_confidence is not None:
            rows.extend(
                row
                for row in db.glossary_entries(book_id, status="proposed")
                if float(row["confidence"] or 0) >= min_confidence
            )
        if not rows:
            raise ConfigError(
                "nothing to approve",
                hint="pass ids or --min-confidence 0.90",
            )
        count = _approve_rows(db, book_id=book_id, rows=rows, approved_by="human")
        _refresh(db, base, book)
        console.print(f"[green]approved {count} glossary entries[/green]")
        blockers = gate_blockers(db, book_id)
        if blockers:
            console.print(
                f"[yellow]{len(blockers)} character/place entries still proposed; "
                "translation will wait on them in manual mode.[/yellow]"
            )
    finally:
        db.close()


@glossary_app.command("reject")
@handle_errors
def reject(
    ctx: typer.Context,
    ids: list[int] = typer.Argument(...),
) -> None:
    """Reject glossary entries by id."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    base, db, book = _workspace(context)
    try:
        count = 0
        for entry_id in ids:
            row = db.get_glossary_entry(entry_id)
            if row is None:
                raise RuntimeFailure(f"glossary entry {entry_id} does not exist")
            db.set_glossary_status(entry_id, "rejected")
            count += 1
        _refresh(db, base, book)
        console.print(f"rejected {count} glossary entries")
    finally:
        db.close()


@glossary_app.command("edit")
@handle_errors
def edit(
    ctx: typer.Context,
    entry_id: int = typer.Argument(...),
    fa: str | None = typer.Option(None, "--fa"),
    en: str | None = typer.Option(None, "--en"),
    kind: str | None = typer.Option(None, "--kind"),
    aliases_fa: str | None = typer.Option(None, "--aliases-fa"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """Edit one glossary entry's Persian form or metadata."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    base, db, book = _workspace(context)
    try:
        if all(value is None for value in (fa, en, kind, aliases_fa, note)):
            raise ConfigError("no edit supplied", hint="pass --fa (or --en/--kind/--note)")
        if kind is not None and kind not in _KINDS:
            raise ConfigError(f"unknown glossary kind: {kind}")
        db.update_glossary_term(
            entry_id,
            term_en=en,
            term_fa=fa,
            kind=kind,
            aliases_fa=_aliases(aliases_fa) if aliases_fa is not None else None,
            notes=note,
        )
        _refresh(db, base, book)
        console.print(f"[green]updated glossary entry {entry_id}[/green]")
    finally:
        db.close()


@glossary_app.command("add")
@handle_errors
def add(
    ctx: typer.Context,
    en: str = typer.Option(..., "--en"),
    fa: str = typer.Option(..., "--fa"),
    kind: str = typer.Option("term", "--kind"),
    aliases_en: str | None = typer.Option(None, "--aliases-en"),
    aliases_fa: str | None = typer.Option(None, "--aliases-fa"),
    note: str | None = typer.Option(None, "--note"),
) -> None:
    """Add an approved glossary entry."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    base, db, book = _workspace(context)
    try:
        if kind not in _KINDS:
            raise ConfigError(f"unknown glossary kind: {kind}")
        entry_id = db.upsert_glossary_entry(
            book_id=int(book["id"]),
            term_en=en,
            term_fa=fa,
            kind=kind,
            status="approved",
            source="manual",
            confidence=1.0,
            aliases_en=_aliases(aliases_en),
            aliases_fa=_aliases(aliases_fa),
            notes=note or "",
            approved_by="human",
            preserve_approved=False,
        )
        _refresh(db, base, book)
        console.print(f"[green]added glossary entry {entry_id}[/green]")
    finally:
        db.close()


@glossary_app.command("import")
@handle_errors
def import_file(
    ctx: typer.Context,
    file: Path = typer.Argument(..., exists=True, readable=True),
    honor_status: bool = typer.Option(False, "--honor-status"),
) -> None:
    """Import a YAML/CSV glossary (approved by default, like `provided` mode)."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    base, db, book = _workspace(context)
    try:
        entries = load_glossary_file(file)
        count = import_entries(
            db,
            book_id=int(book["id"]),
            entries=entries,
            force_status=None if honor_status else "approved",
            source="imported",
            approved_by="imported",
            preserve_approved=False,
        )
        _refresh(db, base, book)
        console.print(f"[green]imported {count} glossary entries[/green]")
    finally:
        db.close()


@glossary_app.command("export")
@handle_errors
def export(
    ctx: typer.Context,
    out: Path | None = typer.Option(None, "--out"),
) -> None:
    """Write artifacts/glossary.yaml (or a chosen path)."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    base, db, book = _workspace(context)
    try:
        if out is None:
            path = write_glossary_artifact(
                db, book_id=int(book["id"]), base=base, book_slug=str(book["slug"])
            )
        else:
            rows = db.glossary_entries(int(book["id"]))
            entries = [
                GlossaryEntry(
                    term_en=str(row["term_en"]),
                    term_fa=str(row["term_fa"] or ""),
                    kind=str(row["kind"]),
                    status=str(row["status"]),
                    confidence=float(row["confidence"] or 0),
                    aliases_en=tuple(json.loads(row["aliases_json"] or "[]")),
                    aliases_fa=tuple(json.loads(row["aliases_fa_json"] or "[]")),
                    gender=str(row["gender"]),
                    notes=str(row["notes"] or ""),
                )
                for row in rows
            ]
            path = write_glossary_yaml(out, book_slug=str(book["slug"]), entries=entries)
        console.print(f"[green]exported[/green] → {path}")
    finally:
        db.close()


@glossary_app.command("sync")
@handle_errors
def sync(ctx: typer.Context) -> None:
    """Import artifacts/glossary.yaml (honoring each entry's status)."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    base, db, book = _workspace(context)
    try:
        count = sync_glossary_artifact(
            db, book_id=int(book["id"]), base=base, book_slug=str(book["slug"])
        )
        console.print(f"[green]synced {count} glossary entries[/green]")
    finally:
        db.close()


@glossary_app.command("pending")
@handle_errors
def pending(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """List translator glossary suggestions found during translation."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    _, db, book = _workspace(context)
    try:
        rows = db.pending_glossary_suggestions(int(book["id"]))
        payload: list[dict[str, Any]] = [
            {
                "id": int(row["id"]),
                "en": str(row["term_en"]),
                "fa": str(row["term_fa"]),
                "kind": str(row["kind"]),
                "reason": str(row["reason"]),
                "segment_id": str(row["segment_id"]),
            }
            for row in rows
        ]
        if json_out:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return
        if not payload:
            console.print("[yellow]no pending glossary suggestions[/yellow]")
            return
        table = Table(title="Pending glossary suggestions")
        table.add_column("#", justify="right")
        table.add_column("en")
        table.add_column("fa")
        table.add_column("kind")
        table.add_column("segment")
        for item in payload:
            table.add_row(
                str(item["id"]),
                item["en"],
                item["fa"],
                item["kind"],
                item["segment_id"],
            )
        console.print(table)
    finally:
        db.close()
