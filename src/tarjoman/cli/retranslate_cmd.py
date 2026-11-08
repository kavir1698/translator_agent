"""`tj retranslate` — targeted redo without touching history (plan 6.10)."""

from __future__ import annotations

import typer

from tarjoman.pipeline.translate_pass import TranslateOptions, run_translate

from .common import build_provider, load_context, parse_int_list, parse_str_list
from .progress import translation_progress
from .ui import console, err_console, handle_errors


@handle_errors
def retranslate(
    ctx: typer.Context,
    segments: str | None = typer.Option(None, "--segments", help="Comma-separated segment ids."),
    chapter: str | None = typer.Option(None, "--chapter", help="Chapter index(es), e.g. 3 or 3,4."),
    window: str | None = typer.Option(None, "--window", help="Window id(s), e.g. c003.w004."),
    stale_only: bool = typer.Option(
        False, "--stale-only", help="Redo windows whose glossary hash changed."
    ),
    progress: bool | None = typer.Option(
        None,
        "--progress/--no-progress",
        help="Show a live progress bar (default: auto when stderr is a terminal).",
    ),
) -> None:
    """Retranslate selected segments, windows or chapters as new versions."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    if not any((segments, chapter, window, stale_only)):
        raise typer.BadParameter(
            "pass --segments, --chapter, --window or --stale-only"
        )
    options = TranslateOptions(
        chapters=parse_int_list(chapter),
        segments=parse_str_list(segments),
        windows=parse_str_list(window),
        only_stale=stale_only,
        force=bool(segments or chapter or window) and not stale_only,
        no_anchors=False,
    )
    with translation_progress(enabled=progress) as reporter:
        result = run_translate(
            root=context.root,
            slug=context.slug,
            settings=context.settings,
            profile=context.profile,
            provider=build_provider(context),
            options=options,
            progress=reporter,
        )
    for warning in result.warnings:
        err_console.print(f"[yellow]warning:[/yellow] {warning}")
    console.print(
        f"retranslated {result.segments_translated} segments · "
        f"coverage {result.coverage:.1%} · ${result.cost_usd:.4f}"
    )
    if result.paused:
        raise typer.Exit(3)
    if result.error_issues:
        raise typer.Exit(4)
