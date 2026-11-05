"""`tj translate` — the main translation pass (M4-M6)."""

from __future__ import annotations

import typer

from tarjoman.pipeline.translate_pass import TranslateOptions, run_translate

from .common import build_provider, load_context, parse_int_list, parse_str_list
from .ui import console, err_console, handle_errors


@handle_errors
def translate(
    ctx: typer.Context,
    chapters: str | None = typer.Option(None, "--chapters", help="Chapter indexes, e.g. 1,3-5."),
    segments: str | None = typer.Option(None, "--segments", help="Comma-separated segment ids."),
    windows: str | None = typer.Option(None, "--windows", help="Comma-separated window ids."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan and estimate; make no calls."),
    exit_after: int | None = typer.Option(
        None, "--exit-after", help="Stop after this many translated windows."
    ),
    max_usd: float | None = typer.Option(None, "--max-usd", help="Override budget.max_usd."),
    cost_mode: str | None = typer.Option(
        None, "--cost-mode", help="auto | economy | standard | quality."
    ),
    allow_overrun: bool = typer.Option(False, "--allow-overrun", help="Ignore the budget cap."),
    force: bool = typer.Option(False, "--force", help="Append new versions for done segments."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass response cache reads."),
    refresh: bool = typer.Option(False, "--refresh", help="Delete cached responses first."),
    parallel_chapters: int | None = typer.Option(
        None, "--parallel-chapters", help="Translate N chapters concurrently (opt-in)."
    ),
    calibrate: bool = typer.Option(
        False, "--calibrate", help="Propose gate-2 anchors from chapter 1 and stop."
    ),
    replan: bool = typer.Option(False, "--replan", help="Accept a changed window plan."),
    only_stale: bool = typer.Option(
        False, "--only-stale", help="Retranslate only glossary-stale segments."
    ),
    require_anchors: bool = typer.Option(
        False, "--require-anchors", help="Fail chapters 2+ when anchors.yaml is missing."
    ),
    no_anchors: bool = typer.Option(False, "--no-anchors", help="Ignore approved anchors."),
    review_brief: bool = typer.Option(
        False, "--review-brief", help="Ask for brief approval first (gate 1a)."
    ),
    local_only: bool = typer.Option(
        False, "--local-only", help="Require a local OpenAI-compatible provider."
    ),
    allow_cloud: bool = typer.Option(
        False, "--allow-cloud", help="Allow cloud providers for licensed books."
    ),
    accept_pdf_quality: bool = typer.Option(
        False, "--accept-pdf-quality", help="Proceed with a low-quality PDF import."
    ),
) -> None:
    """Translate the book (or a chapter subset) window by window."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    if cost_mode is not None and cost_mode not in {"auto", "economy", "standard", "quality"}:
        raise typer.BadParameter("--cost-mode must be auto, economy, standard or quality")
    if local_only and context.settings.provider.profile != "local":
        raise typer.BadParameter("--local-only requires provider.profile: local")
    if require_anchors and no_anchors:
        raise typer.BadParameter("--require-anchors and --no-anchors are mutually exclusive")
    _ = allow_cloud
    options = TranslateOptions(
        chapters=parse_int_list(chapters),
        segments=parse_str_list(segments),
        windows=parse_str_list(windows),
        dry_run=dry_run,
        exit_after=exit_after,
        max_usd=max_usd,
        cost_mode=cost_mode,
        allow_overrun=allow_overrun,
        force=force,
        no_cache=no_cache,
        refresh=refresh,
        parallel_chapters=parallel_chapters or context.settings.concurrency.parallel_chapters,
        calibrate=calibrate,
        replan=replan,
        only_stale=only_stale,
        require_anchors=require_anchors,
        no_anchors=no_anchors,
        accept_pdf_quality=accept_pdf_quality,
    )
    for warning in options_warnings(review_brief):
        err_console.print(f"[yellow]{warning}[/yellow]")
    result = run_translate(
        root=context.root,
        slug=context.slug,
        settings=context.settings,
        profile=context.profile,
        provider=build_provider(context),
        options=options,
    )
    for warning in result.warnings:
        err_console.print(f"[yellow]warning:[/yellow] {warning}")
    if result.dry_run:
        estimate = result.estimate
        console.print(
            f"[bold]dry run[/bold] · {estimate.get('words', 0)} words · "
            f"{estimate.get('mode')} mode · ~{estimate.get('calls', 0)} calls · "
            f"${estimate.get('offpeak_usd', 0):.2f} off-peak / ${estimate.get('peak_usd', 0):.2f} peak"
        )
        return
    console.print(
        f"run [bold]{result.run_id}[/bold] · {result.mode} mode · {result.chapters} chapters · "
        f"{result.windows} windows · {result.segments_translated} segments · "
        f"coverage {result.coverage:.1%} · ${result.cost_usd:.4f} "
        f"({result.cached_calls}/{result.calls} cached)"
    )
    if result.report_path is not None:
        console.print(f"report → {result.report_path}")
    if result.paused:
        err_console.print(
            "[yellow]paused at the budget cap; state is saved.[/yellow] "
            "Raise budget.max_usd or pass --allow-overrun, then run `tj translate` again."
        )
        raise typer.Exit(3)
    if result.error_issues or result.coverage < 1.0:
        err_console.print(
            f"[yellow]incomplete:[/yellow] {result.error_issues} open error issues, "
            f"coverage {result.coverage:.1%}. See {result.report_path}"
        )
        raise typer.Exit(4)


def options_warnings(review_brief: bool) -> list[str]:
    warnings: list[str] = []
    if review_brief:
        warnings.append(
            "--review-brief does not block translation; use `tj analyze --review-brief` "
            "for the manual gate."
        )
    return warnings
