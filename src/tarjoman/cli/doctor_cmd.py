"""`tj doctor` — environment and configuration health."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import typer
from rich.table import Table

from tarjoman.core.config import Settings, api_key_for
from tarjoman.core.cost import ModelProfile
from tarjoman.core.errors import TarjomanError
from tarjoman.core.paths import book_dir
from tarjoman.llm.promptlib import PROMPTS_DIR, load_all

from .common import load_context
from .ui import console, handle_errors

OK = "ok"
WARN = "warn"
FAIL = "fail"
_SYMBOL = {OK: "[green]ok[/green]", WARN: "[yellow]warn[/yellow]", FAIL: "[red]fail[/red]"}


@dataclass
class Check:
    name: str
    status: str
    detail: str


@handle_errors
def doctor(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
    models: bool = typer.Option(False, "--models", help="Resolve and ping model aliases."),
    tokenizer: bool = typer.Option(False, "--tokenizer", help="Report token calibration."),
    epubcheck: bool = typer.Option(False, "--epubcheck", help="Check the epubcheck binary."),
) -> None:
    """Report Python, SQLite, config, models, prompts, tools and keys (never values)."""
    checks: list[Check] = []

    py_ok = sys.version_info >= (3, 12)
    checks.append(Check("python", OK if py_ok else FAIL, sys.version.split()[0]))

    checks.append(Check("sqlite", OK, sqlite3.sqlite_version))
    try:
        disk = shutil.disk_usage(Path.cwd())
        checks.append(Check("disk_free", OK if disk.free > 1_000_000_000 else WARN, f"{disk.free / 1e9:.1f} GB"))
    except OSError as exc:
        checks.append(Check("disk_free", WARN, str(exc)))

    try:
        context = load_context(ctx.obj.get("book") if ctx.obj else None)
    except TarjomanError as exc:
        checks.append(Check("config", FAIL, str(exc)))
        _render(checks, json_out)
        raise typer.Exit(2) from exc

    settings = context.settings
    checks.append(Check("config", OK, f"{len(settings.model_dump())} sections valid"))
    checks.append(Check("profile", OK, f"{context.profile.provider} ({', '.join(context.profile.tiers)})"))
    checks.append(Check("thinking", OK, settings.provider.thinking))

    prompt_error = None
    try:
        prompts = load_all(PROMPTS_DIR)
    except TarjomanError as exc:
        prompts = {}
        prompt_error = exc
    checks.append(
        Check(
            "prompts",
            OK if len(prompts) >= 12 else FAIL,
            prompt_error.message if prompt_error else f"{len(prompts)} loaded",
        )
    )

    providers = {context.profile.provider_for(tier) for tier in context.profile.tiers.values()}
    keys = {provider: api_key_for(provider) for provider in sorted(providers)}
    missing = [provider for provider, value in keys.items() if not value]
    checks.append(
        Check(
            "api_key",
            OK if not missing else WARN,
            "present for " + ", ".join(sorted(keys))
            if not missing
            else f"missing for {', '.join(missing)} (offline/mock mode)",
        )
    )

    if models:
        checks.extend(_model_checks(settings, context.profile))

    if tokenizer:
        from tarjoman.core.tokens import TokenRatios

        ratios = TokenRatios()
        checks.append(
            Check(
                "tokenizer",
                OK,
                f"en={ratios.en_tokens_per_char}/char fa={ratios.fa_tokens_per_char}/char "
                "(recalibrated from usage after 50 calls)",
            )
        )

    if epubcheck:
        binary = shutil.which("epubcheck")
        checks.append(Check("epubcheck", OK if binary else WARN, binary or "not found (optional)"))
        soffice = shutil.which("soffice")
        checks.append(Check("libreoffice", OK if soffice else WARN, soffice or "not found (PDF export only)"))

    book_path = (
        book_dir(context.slug, context.root, settings.project.books_dir) if context.slug else None
    )
    if book_path and (book_path / "book.db").exists():
        from tarjoman.core.db import Database

        with Database(book_path / "book.db") as db:
            book = db.get_book(context.slug or "")
            if book is not None:
                checks.append(Check("provenance", OK, f"rights_basis={book['rights_basis']}"))

    failed = _render(checks, json_out)
    if failed:
        raise typer.Exit(2)


def _model_checks(settings: Settings, profile: ModelProfile) -> list[Check]:
    from tarjoman.llm.litellm_provider import LiteLLMProvider
    from tarjoman.llm.provider import Message

    checks: list[Check] = []
    for alias, tier in profile.tiers.items():
        provider_name = profile.provider_for(tier)
        if not api_key_for(provider_name):
            checks.append(
                Check(
                    f"model:{alias}",
                    WARN,
                    f"{tier.model} via {provider_name} (ping skipped: no key)",
                )
            )
            continue
        provider = LiteLLMProvider(
            profile=profile,
            timeout_s=min(settings.provider.timeout_s, 30),
            max_retries=0,
            thinking=settings.provider.thinking,
        )
        try:
            provider.complete(
                messages=[Message(role="user", content="Reply with the word ok.")],
                model=tier.model,
                temperature=0.0,
                max_output_tokens=5,
            )
            checks.append(Check(f"model:{alias}", OK, f"{tier.model} responded"))
        except TarjomanError as exc:
            checks.append(Check(f"model:{alias}", FAIL, f"{tier.model}: {exc.message}"))
    return checks


def _render(checks: list[Check], json_out: bool) -> bool:
    failed = any(check.status == FAIL for check in checks)
    if json_out:
        typer.echo(json.dumps({"checks": [asdict(check) for check in checks], "failed": failed}, indent=2))
        return failed
    table = Table(title="tj doctor", show_lines=False)
    table.add_column("check", style="bold")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    for check in checks:
        table.add_row(check.name, _SYMBOL.get(check.status, check.status), check.detail)
    console.print(table)
    if failed:
        console.print("[red]environment is not ready[/red]")
    return failed
