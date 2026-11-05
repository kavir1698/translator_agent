"""`tj audit` — whole-book consistency audit and Persian read-through (M9)."""

from __future__ import annotations

import json

import typer

from tarjoman.pipeline.audit_pass import AuditOptions, run_audit

from .common import build_provider, load_context
from .ui import console, handle_errors


@handle_errors
def audit(
    ctx: typer.Context,
    chapter: int | None = typer.Option(None, "--chapter", help="Audit one chapter index."),
    sample: int = typer.Option(3, "--sample", help="Sampled windows per chapter."),
    readthrough: bool | None = typer.Option(
        None,
        "--readthrough/--no-readthrough",
        help="Run the source-free Persian read-through (default: settings.pipeline.passes.readthrough).",
    ),
    max_usd: float | None = typer.Option(None, "--max-usd", help="Override budget.max_usd."),
    json_output: bool = typer.Option(False, "--json", help="Print a JSON summary."),
) -> None:
    """Audit consistency, read the Persian, and repair only flagged segments."""
    if sample < 0:
        raise typer.BadParameter("--sample must be zero or more")
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    options = AuditOptions(
        chapter=chapter,
        sample=sample,
        readthrough=readthrough,
        max_usd=max_usd,
    )
    result = run_audit(
        root=context.root,
        slug=context.slug,
        settings=context.settings,
        profile=context.profile,
        provider=build_provider(context),
        options=options,
    )
    for warning in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {warning}")
    if json_output:
        console.print(
            json.dumps(
                {
                    "run_id": result.run_id,
                    "status": result.status,
                    "chapters": result.chapters,
                    "a10_findings": result.a10_findings,
                    "a11_issues": result.a11_issues,
                    "deterministic_issues": result.deterministic_issues,
                    "repairs": result.repairs,
                    "cost_usd": result.cost_usd,
                    "coverage": result.coverage,
                    "adherence": result.adherence,
                    "report_path": str(result.report_path) if result.report_path else None,
                },
                ensure_ascii=False,
            )
        )
        return
    console.print(
        f"run [bold]{result.run_id}[/bold] · {result.chapters} chapters · "
        f"A10 {result.a10_findings} findings · A11 {result.a11_issues} issues · "
        f"deterministic {result.deterministic_issues} · {result.repairs} repairs · "
        f"adherence {result.adherence:.1%} · ${result.cost_usd:.4f}"
    )
    if result.report_path is not None:
        console.print(f"report → {result.report_path}")
