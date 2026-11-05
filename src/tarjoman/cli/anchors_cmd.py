"""`tj anchors` — gate 2 style-anchor calibration."""

from __future__ import annotations

import typer

from tarjoman.core.db import Database
from tarjoman.core.errors import ConfigError
from tarjoman.core.paths import find_book_dir
from tarjoman.pipeline.anchors_pass import (
    approve_anchors,
    list_anchors,
    propose_anchors,
    reject_anchors,
)

from .common import Context, load_context, parse_str_list
from .ui import console, handle_errors

anchors_app = typer.Typer(name="anchors", help="Propose, approve or reject style anchors.")


def _open(ctx: typer.Context) -> tuple[Context, Database, int]:
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    base = find_book_dir(context.root, context.slug, context.settings.project.books_dir)
    db = Database(base / "book.db")
    db.initialize()
    book = db.fetchone("SELECT * FROM books LIMIT 1")
    if book is None:
        db.close()
        raise ConfigError("the workspace has no book row", hint="re-run `tj init`")
    return context, db, int(book["id"])


@anchors_app.command("propose")
@handle_errors
def propose(ctx: typer.Context) -> None:
    """Select up to three deterministic anchor candidates from chapter 1."""
    context, db, book_id = _open(ctx)
    try:
        result = propose_anchors(
            root=context.root,
            slug=context.slug,
            db=db,
            book_id=book_id,
        )
    finally:
        db.close()
    console.print(f"{result.proposed} anchor candidates → {result.path}")


@anchors_app.command("approve")
@handle_errors
def approve(
    ctx: typer.Context,
    ids: str | None = typer.Option(None, "--ids", help="Candidate ids to approve (default: all)."),
) -> None:
    """Write artifacts/anchors.yaml (requires at least two pairs)."""
    _context, db, book_id = _open(ctx)
    try:
        result = approve_anchors(db=db, book_id=book_id, ids=parse_str_list(ids))
    finally:
        db.close()
    console.print(f"{result.approved} anchors approved → {result.path}")


@anchors_app.command("reject")
@handle_errors
def reject(
    ctx: typer.Context,
    ids: str = typer.Option(..., "--ids", help="Candidate ids to reject."),
) -> None:
    """Reject anchor candidates."""
    _context, db, book_id = _open(ctx)
    try:
        result = reject_anchors(db=db, book_id=book_id, ids=parse_str_list(ids) or [])
    finally:
        db.close()
    console.print(f"{result.rejected} candidates rejected → {result.path}")


@anchors_app.command("list")
@handle_errors
def list_cmd(ctx: typer.Context) -> None:
    """List approved anchors and the current draft."""
    _context, db, _book_id = _open(ctx)
    try:
        data = list_anchors(db=db)
    finally:
        db.close()
    if data["approved"]:
        console.print("[bold]approved[/bold]")
        for anchor in data["approved"]:
            console.print(f"  {anchor.get('id')} · {anchor.get('note', '')}")
    else:
        console.print("no approved anchors")
    if data["draft"]:
        console.print("[bold]draft[/bold]")
        for candidate in data["draft"]:
            console.print(
                f"  {candidate.get('id')} · {candidate.get('status')} · {candidate.get('segment_id')}"
            )
