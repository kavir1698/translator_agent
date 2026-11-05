"""`tj init` — create a book workspace from an EPUB, PDF, DOCX, TXT or Markdown file."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

from tarjoman.core.config import load_settings
from tarjoman.core.paths import project_root
from tarjoman.pipeline.init_pass import init_book

from .ui import console, handle_errors


@handle_errors
def init(
    ctx: typer.Context,
    file: Path = typer.Argument(..., exists=True, readable=True, help="Source book file."),
    slug: str | None = typer.Option(None, "--slug", help="Workspace slug (default: from the title)."),
    rights_basis: str | None = typer.Option(
        None, "--rights-basis", help="public_domain | licensed | user_supplied (informational)."
    ),
    translator: str | None = typer.Option(None, "--translator", help="dc:contributor value."),
    glossary: Path | None = typer.Option(
        None, "--glossary", help="Approved glossary file (enables glossary.mode=provided)."
    ),
    force: bool = typer.Option(False, "--force", help="Archive an existing workspace and rebuild."),
) -> None:
    """Extract the source, create the workspace and segment it. Never calls an LLM."""
    root = project_root()
    book_overrides: dict[str, Any] = {}
    if rights_basis:
        book_overrides["rights_basis"] = rights_basis
    if translator:
        book_overrides["translator"] = translator
    if slug:
        book_overrides["slug"] = slug
    overrides: dict[str, Any] | None = {"book": book_overrides} if book_overrides else None
    settings = load_settings(root=root, overrides=overrides)

    result = init_book(
        root=root,
        source_path=file,
        settings=settings,
        slug=slug,
        force=force,
        glossary_path=str(glossary) if glossary else None,
    )
    if not result.created:
        console.print(
            f"[yellow]{result.slug}[/yellow] is already initialized from this exact file "
            f"({result.chapters} chapters, {result.segments} segments); nothing to do."
        )
        return
    quality = (
        f", PDF import quality {result.import_quality:.2f}"
        if result.import_quality is not None
        else ""
    )
    console.print(
        f"[green]initialized[/green] [bold]{result.slug}[/bold] "
        f"({result.source_format}{quality})"
    )
    console.print(
        f"  {result.chapters} chapters · {result.segments} segments · {result.words:,} words\n"
        f"  workspace: {result.db_path.parent}\n"
        f"  next: [bold]tj estimate[/bold], then [bold]tj translate --chapters 1[/bold]"
    )
