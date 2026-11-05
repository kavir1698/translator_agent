"""`tj export` — EPUB 3, DOCX, Markdown and plain text (no LLM, read-only)."""

from __future__ import annotations

from pathlib import Path

import typer

from tarjoman.pipeline.export_pass import ExportOptions, run_export

from .common import load_context, parse_str_list
from .ui import console, handle_errors


@handle_errors
def export(
    ctx: typer.Context,
    formats: str | None = typer.Option(
        None,
        "--formats",
        help="Comma-separated formats: epub,docx,md,txt (default: config export.formats).",
    ),
    out: Path | None = typer.Option(
        None, "--out", help="Output directory (default: the book workspace's out/)."
    ),
    allow_incomplete: bool = typer.Option(
        False, "--allow-incomplete", help="Export even when coverage or issues block it."
    ),
    generated_at: str | None = typer.Option(
        None,
        "--generated-at",
        help="ISO-8601 timestamp for dcterms:modified (default: newest translation).",
    ),
    pdf: bool = typer.Option(
        False,
        "--pdf",
        help="Also produce a PDF via LibreOffice; prints the manual command when absent. "
        "The result is readable but not print-ready.",
    ),
    bilingual: bool = typer.Option(
        False,
        "--bilingual",
        help="Also produce the self-contained bilingual HTML edition (off by default).",
    ),
) -> None:
    """Build the reading and text editions from the current translations."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    result = run_export(
        root=context.root,
        slug=context.slug,
        settings=context.settings,
        options=ExportOptions(
            formats=parse_str_list(formats),
            out=out,
            pdf=pdf,
            bilingual=bilingual,
            allow_incomplete=allow_incomplete,
            generated_at=generated_at,
        ),
    )
    for name, path in result.paths.items():
        console.print(f"[green]{name}[/green] → {path}")
    for warning in result.warnings:
        console.print(f"[yellow]warning[/yellow] {warning}")
    if result.incomplete:
        console.print("[yellow]warning[/yellow] translation is incomplete; review the output")
