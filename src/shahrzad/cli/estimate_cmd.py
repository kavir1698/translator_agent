"""`shz estimate` — token table and costs at both tariffs, before spending."""

from __future__ import annotations

import json
from dataclasses import asdict

import typer
from rich.table import Table

from shahrzad.core.cost import estimate_all_modes, is_peak, select_cost_mode
from shahrzad.core.db import Database
from shahrzad.core.paths import book_dir
from shahrzad.core.tokens import count_words

from .common import Context, load_context
from .ui import console, handle_errors

DEFAULT_WORDS = 90_000


def _book_words(context: Context, max_chapter: int | None) -> tuple[int, str]:
    if not context.slug:
        return DEFAULT_WORDS, "default 90k-word book"
    db_path = book_dir(context.slug, context.root, context.settings.project.books_dir) / "book.db"
    if not db_path.exists():
        return DEFAULT_WORDS, "default 90k-word book (workspace has no DB)"
    with Database(db_path) as db:
        book = db.get_book(context.slug)
        if book is None:
            return DEFAULT_WORDS, "default 90k-word book (book row missing)"
        sql = (
            "SELECT s.source_text FROM segments s JOIN chapters c ON c.id = s.chapter_id "
            "WHERE c.book_id = ?"
        )
        params: list[object] = [book["id"]]
        if max_chapter is not None:
            sql += " AND c.idx <= ?"
            params.append(max_chapter)
        rows = db.fetchall(sql, params)
    words = sum(count_words(row["source_text"]) for row in rows)
    if words == 0:
        return DEFAULT_WORDS, "default 90k-word book (no segments yet)"
    scope = f"chapters 1..{max_chapter}" if max_chapter is not None else "all chapters"
    return words, f"{context.slug} ({scope}, {len(rows)} segments)"


@handle_errors
def estimate(
    ctx: typer.Context,
    chapters: int | None = typer.Option(None, "--chapters", help="Estimate the first N chapters."),
    words: int | None = typer.Option(None, "--words", help="Override the word count (fixtures)."),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Print the token table plus all three cost modes at peak and off-peak tariffs."""
    context = load_context(ctx.obj.get("book") if ctx.obj else None)
    profile = context.profile
    settings = context.settings

    if words is not None:
        count, scope = words, "--words override"
    else:
        count, scope = _book_words(context, chapters)

    modes = estimate_all_modes(count, profile)
    peak_now = is_peak()
    chosen, reason = select_cost_mode(
        count, profile, settings.budget.max_usd, peak=peak_now
    )

    if json_out:
        payload = {
            "words": count,
            "scope": scope,
            "tariff_now": "peak" if peak_now else "offpeak",
            "budget_cap_usd": settings.budget.max_usd,
            "auto_mode": chosen,
            "auto_reason": reason,
            "modes": {
                mode: {
                    "calls": item.calls,
                    "tokens_in": item.tokens_in,
                    "tokens_out": item.tokens_out,
                    "peak_usd": item.peak_usd,
                    "offpeak_usd": item.offpeak_usd,
                    "passes": [asdict(pass_item) for pass_item in item.passes],
                }
                for mode, item in modes.items()
            },
        }
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    console.print(
        f"[bold]{count:,}[/bold] source words — {scope}\n"
        f"budget cap [bold]${settings.budget.max_usd:g}[/bold] · "
        f"tariff now [bold]{'peak' if peak_now else 'off-peak'}[/bold] · "
        f"auto mode [bold]{chosen}[/bold]"
    )
    console.print(f"[dim]{reason}[/dim]")

    tokens = Table(title="Token table (standard mode)", show_lines=False)
    tokens.add_column("pass")
    tokens.add_column("tier")
    tokens.add_column("calls", justify="right")
    tokens.add_column("input", justify="right")
    tokens.add_column("output", justify="right")
    for item in modes["standard"].passes:
        tokens.add_row(
            item.name,
            item.tier,
            f"{item.calls:,}",
            f"{item.tokens_in:,}",
            f"{item.tokens_out:,}",
        )
    total = modes["standard"]
    tokens.add_row(
        "[bold]total[/bold]",
        "",
        f"[bold]{total.calls:,}[/bold]",
        f"[bold]{total.tokens_in:,}[/bold]",
        f"[bold]{total.tokens_out:,}[/bold]",
    )
    console.print(tokens)

    costs = Table(title="Cost by mode and tariff", show_lines=False)
    costs.add_column("mode")
    costs.add_column("peak (USD)", justify="right")
    costs.add_column("off-peak (USD)", justify="right")
    for mode in ("economy", "standard", "quality"):
        mode_est = modes[mode]
        costs.add_row(mode, f"${mode_est.peak_usd:.2f}", f"${mode_est.offpeak_usd:.2f}")
    console.print(costs)
