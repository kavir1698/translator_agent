"""`shz analyze` — brief, chapter summaries, entities and optional glossary (M3)."""

from __future__ import annotations

import typer

from shahrzad.pipeline.analyze_pass import run_analyze

from .common import build_provider, load_context
from .ui import console, handle_errors


@handle_errors
def analyze(
    ctx: typer.Context,
    sample_budget: int | None = typer.Option(
        None, "--sample-budget", help="Override pipeline.analyze.sample_token_budget."
    ),
    draft_only: bool = typer.Option(
        False, "--draft-only", help="Write artifacts/brief.draft.yaml and stop."
    ),
    approve: bool = typer.Option(
        False, "--approve", help="Validate and freeze the existing brief draft."
    ),
    only: int | None = typer.Option(
        None, "--only", help="Analyze only this chapter index (summaries and entities)."
    ),
    review_brief: bool = typer.Option(
        False, "--review-brief", help="Manual Gate 1a: pause after the brief draft."
    ),
    auto_glossary: bool = typer.Option(
        False, "--auto-glossary", help="Use glossary.mode=auto for this run."
    ),
) -> None:
    """Build the brief, one summary per chapter, entities and optional glossary."""
    if approve and (draft_only or review_brief or auto_glossary):
        raise typer.BadParameter("--approve cannot be combined with analysis flags")
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    result = run_analyze(
        root=context.root,
        slug=context.slug,
        settings=context.settings,
        profile=context.profile,
        provider=build_provider(context),
        approve=approve,
        draft_only=draft_only,
        review_brief=review_brief,
        only_chapter=only,
        auto_glossary=auto_glossary,
        sample_budget=sample_budget,
    )

    if approve or result.frozen:
        if result.brief_path is not None:
            console.print(f"[green]brief frozen[/green] → {result.brief_path}")
        else:
            console.print("[green]brief already approved[/green]")
        if approve:
            return

    if result.draft_path is not None and not result.frozen:
        console.print(f"[yellow]brief draft[/yellow] → {result.draft_path}")

    if result.paused_for_brief:
        console.print(
            "[yellow]Gate 1a is waiting for approval.[/yellow] "
            "Review the draft, then run [bold]shz analyze --approve[/bold]."
        )
        raise typer.Exit(3)

    if result.summaries or result.entities or result.glossary_entries:
        console.print(
            f"{result.summaries} summaries · {result.entities} entity candidates · "
            f"{result.proposals} proposals · {result.glossary_entries} glossary entries "
            f"([bold]{result.mode}[/bold] mode)"
        )
    if result.proper_nouns:
        console.print(f"proper-noun list: {result.proper_nouns} names")
    if result.run_id:
        console.print(
            f"run [bold]{result.run_id}[/bold] · {result.calls} calls "
            f"({result.cached_calls} cached) · ${result.cost_usd:.4f}"
        )
