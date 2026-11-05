"""`tj review` — build the HTML review report and apply corrections."""

from __future__ import annotations

from pathlib import Path

import typer

from tarjoman.review.corrections import apply_corrections
from tarjoman.review.report import ReviewBuildOptions, build_report

from .common import load_context
from .ui import console, handle_errors

review_app = typer.Typer(
    name="review",
    help="Build the flagged-segment report and apply human corrections.",
    no_args_is_help=True,
)


@review_app.command("build")
@handle_errors
def build(
    ctx: typer.Context,
    chapter: int | None = typer.Option(None, "--chapter", help="Limit to one chapter index."),
    out: Path | None = typer.Option(None, "--out", help="Report path (default: review/report.html)."),
) -> None:
    """Refresh QA and render a self-contained review/report.html."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    result = build_report(
        root=context.root,
        slug=context.slug,
        settings=context.settings,
        options=ReviewBuildOptions(chapter=chapter, out=out),
    )
    console.print(
        f"{result.rows} rows · coverage {result.coverage:.1%} · "
        f"glossary {result.adherence:.1%} · {result.issues} open issues "
        f"({result.errors} error, {result.criticals} critical) · "
        f"${result.cost_usd:.4f}"
    )
    if result.prompt_versions:
        console.print(f"prompt versions: {', '.join(result.prompt_versions)}")
    console.print(f"[green]report[/green] → {result.path}")
    console.print("Next: review the file, download corrections.jsonl, then run `tj review apply`.")


@review_app.command("apply")
@handle_errors
def apply(
    ctx: typer.Context,
    corrections: Path = typer.Argument(
        ..., exists=True, readable=True, help="corrections.jsonl from the review report."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate without writing anything."),
    reviewer: str | None = typer.Option(None, "--reviewer", help="Reviewer name."),
    force: bool = typer.Option(
        False, "--force", help="Allow a correction whose reviewed version is no longer current."
    ),
) -> None:
    """Insert human versions, re-run QA and update review/flags.csv."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    result = apply_corrections(
        root=context.root,
        slug=context.slug,
        settings=context.settings,
        corrections_path=corrections,
        dry_run=dry_run,
        reviewer=reviewer,
        force=force,
    )
    if dry_run:
        console.print(
            f"validated {result.applied} accept and {result.needs_work} needs_work "
            "correction(s); no changes written"
        )
        return
    console.print(
        f"applied {result.applied} accept · {result.needs_work} needs work · "
        f"{result.resolved} issues resolved · {result.new_versions} new version(s) · "
        f"{result.flags_updated} flag row(s) updated"
    )
    console.print("Next: run `tj export` when coverage and issues look right.")
