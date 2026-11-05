"""`tj qa` — deterministic lint, flags, and issue lifecycle."""

from __future__ import annotations

import json

import typer

from tarjoman.pipeline.qa_pass import QaOptions, accept_issue, resolve_issue, run_qa

from .common import load_context
from .ui import console, handle_errors

qa_app = typer.Typer(
    name="qa",
    help="Run deterministic linters and manage findings.",
    no_args_is_help=False,
    invoke_without_command=True,
)


@qa_app.callback(invoke_without_command=True)
@handle_errors
def qa(
    ctx: typer.Context,
    chapter: int | None = typer.Option(None, "--chapter", help="Limit to one chapter index."),
    group: str | None = typer.Option(None, "--group", help="consistency | literary | loss | all."),
    stale_glossary: bool = typer.Option(
        False, "--stale-glossary", help="Mark translations older than the approved glossary."
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Lint every stored translation and write review/flags.csv."""
    if ctx.invoked_subcommand is not None:
        return
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    result = run_qa(
        root=context.root,
        slug=context.slug,
        settings=context.settings,
        options=QaOptions(
            chapter=chapter,
            group=group,
            stale_glossary=stale_glossary,
            json_output=json_output,
        ),
    )
    if json_output:
        console.print(
            json.dumps(
                {
                    "book": result.book_slug,
                    "issues": result.issues,
                    "errors": result.errors,
                    "criticals": result.criticals,
                    "coverage": round(result.coverage, 4),
                    "stale": result.stale,
                    "flags": result.flags,
                    "flags_path": str(result.flags_path) if result.flags_path else None,
                    "groups": result.groups,
                },
                ensure_ascii=False,
            ),
            soft_wrap=True,
        )
        return
    console.print(
        f"{result.issues} open issues ({result.errors} error, {result.criticals} critical) · "
        f"coverage {result.coverage:.1%} · {result.flags} flags"
    )
    for name, count in sorted(result.groups.items()):
        if count:
            console.print(f"  {name}: {count} segments")
    if result.stale:
        console.print(f"  stale glossary versions: {result.stale}")
    if result.flags_path is not None:
        console.print(f"flags → {result.flags_path}")


@qa_app.command("accept")
@handle_errors
def accept(
    ctx: typer.Context,
    issue: int = typer.Option(..., "--issue", help="Issue id to waive."),
    note: str = typer.Option(..., "--note", help="Why the known error is acceptable."),
    reviewer: str = typer.Option(..., "--reviewer", help="Reviewer name."),
) -> None:
    """Waive an issue; the waiver is recorded and remains visible."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    accept_issue(
        root=context.root,
        slug=context.slug,
        settings=context.settings,
        issue_id=issue,
        note=note,
        reviewer=reviewer,
    )
    console.print(f"issue {issue} waived by {reviewer}")


@qa_app.command("resolve")
@handle_errors
def resolve(
    ctx: typer.Context,
    issue: int = typer.Option(..., "--issue", help="Issue id to resolve."),
) -> None:
    """Mark an issue resolved."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    resolve_issue(
        root=context.root,
        slug=context.slug,
        settings=context.settings,
        issue_id=issue,
    )
    console.print(f"issue {issue} resolved")
