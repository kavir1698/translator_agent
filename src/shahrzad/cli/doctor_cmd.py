"""`shz doctor` — environment and configuration health."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import shutil
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

import typer
from rich.table import Table

import shahrzad
from shahrzad.core.config import Settings, api_key_for
from shahrzad.core.cost import ModelProfile
from shahrzad.core.db import SCHEMA_VERSION
from shahrzad.core.errors import ShahrzadError
from shahrzad.core.paths import book_dir, books_dir
from shahrzad.llm.promptlib import PROMPTS_DIR, load_all

from .common import load_context
from .ui import console, handle_errors

OK = "ok"
INFO = "info"
WARN = "warn"
FAIL = "fail"
_SYMBOL = {
    OK: "[green]ok[/green]",
    INFO: "[blue]info[/blue]",
    WARN: "[yellow]warn[/yellow]",
    FAIL: "[red]fail[/red]",
}
_REQUIRED_PACKAGES = (
    ("python-docx", "docx"),
    ("pypdfium2", "pypdfium2"),
    ("litellm", "litellm"),
)
_EXPORT_ASSETS = (
    "Vazirmatn-Regular.woff2",
    "Vazirmatn-Bold.woff2",
    "OFL.txt",
)


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
    checks.append(Check("package", OK, f"shahrzad {shahrzad.__version__}"))

    checks.append(Check("sqlite", OK, sqlite3.sqlite_version))
    try:
        disk = shutil.disk_usage(Path.cwd())
        checks.append(
            Check(
                "disk_free",
                OK if disk.free > 1_000_000_000 else WARN,
                f"{disk.free / 1e9:.1f} GB",
            )
        )
    except OSError as exc:
        checks.append(Check("disk_free", WARN, str(exc)))

    try:
        context = load_context(ctx.obj.get("book") if ctx.obj else None)
    except ShahrzadError as exc:
        checks.append(Check("config", FAIL, str(exc)))
        _render(checks, json_out)
        raise typer.Exit(2) from exc

    settings = context.settings
    checks.append(Check("config", OK, f"{len(settings.model_dump())} sections valid"))
    checks.append(
        Check("profile", OK, f"{context.profile.provider} ({', '.join(context.profile.tiers)})")
    )
    checks.append(Check("thinking", OK, settings.provider.thinking))
    checks.extend(_package_checks())
    checks.extend(_books_dir_checks(settings, context.root))
    checks.extend(_asset_checks())

    prompt_error = None
    try:
        prompts = load_all(PROMPTS_DIR)
    except ShahrzadError as exc:
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
        from shahrzad.core.tokens import TokenRatios

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
        checks.append(
            Check("libreoffice", OK if soffice else WARN, soffice or "not found (PDF export only)")
        )

    book_path = (
        book_dir(context.slug, context.root, settings.project.books_dir) if context.slug else None
    )
    if book_path is not None and (book_path / "book.db").exists():
        checks.extend(_workspace_checks(book_path, context.slug or ""))

    failed = _render(checks, json_out)
    if failed:
        raise typer.Exit(2)


def _package_checks() -> list[Check]:
    checks: list[Check] = []
    for distribution, module in _REQUIRED_PACKAGES:
        try:
            importlib.import_module(module)
            version = importlib.metadata.version(distribution)
        except Exception as exc:  # noqa: BLE001 - any import failure is a broken install
            checks.append(Check(f"package:{distribution}", FAIL, f"not importable: {exc}"))
        else:
            checks.append(Check(f"package:{distribution}", OK, version))
    return checks


def _books_dir_checks(settings: Settings, root: Path) -> list[Check]:
    path = books_dir(root, settings.project.books_dir)
    probe = path / f".write-test-{uuid4().hex[:8]}"
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return [Check("books_dir", FAIL, str(exc))]
    return [Check("books_dir", OK, f"{path} (writable)")]


def _asset_checks() -> list[Check]:
    assets = Path(shahrzad.__file__).resolve().parent / "export" / "epub_assets"
    missing = [name for name in _EXPORT_ASSETS if not (assets / name).is_file()]
    detail = (
        f"missing: {', '.join(missing)}"
        if missing
        else "Vazirmatn Regular/Bold + OFL present"
    )
    return [Check("export_fonts", OK if not missing else FAIL, detail)]


def _workspace_checks(base: Path, slug: str) -> list[Check]:
    checks: list[Check] = []
    db_path = base / "book.db"
    try:
        connection = sqlite3.connect(db_path)
        try:
            quick = connection.execute("PRAGMA quick_check").fetchone()
            healthy = bool(quick) and str(quick[0]).lower() == "ok"
            checks.append(
                Check(
                    "sqlite_check",
                    OK if healthy else FAIL,
                    "quick_check ok" if healthy else f"quick_check: {quick[0] if quick else 'empty'}",
                )
            )
            try:
                version_row = connection.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()
                version = int(version_row[0]) if version_row and version_row[0] is not None else 0
            except sqlite3.Error as exc:
                checks.append(Check("schema", FAIL, f"schema_version unreadable: {exc}"))
                version = 0
            else:
                checks.append(
                    Check(
                        "schema",
                        OK if version == SCHEMA_VERSION else WARN,
                        f"v{version} (expected v{SCHEMA_VERSION})",
                    )
                )
            book = connection.execute(
                "SELECT id, title_en FROM books WHERE slug = ? LIMIT 1", (slug,)
            ).fetchone()
            if book is None:
                checks.append(Check("workspace", FAIL, f"no book row for {slug!r}"))
            else:
                book_id = int(book[0])
                chapters = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chapters WHERE book_id = ?", (book_id,)
                    ).fetchone()[0]
                )
                segments = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM segments s JOIN chapters c ON c.id = s.chapter_id "
                        "WHERE c.book_id = ?",
                        (book_id,),
                    ).fetchone()[0]
                )
                checks.append(
                    Check(
                        "workspace",
                        OK,
                        f"{slug}: {chapters} chapters, {segments} segments",
                    )
                )
        finally:
            connection.close()
    except sqlite3.Error as exc:
        checks.append(Check("workspace", FAIL, f"cannot read {db_path}: {exc}"))
        return checks

    artifacts = base / "artifacts"
    checks.append(
        Check(
            "artifact:brief",
            OK if (artifacts / "brief.yaml").is_file() else INFO,
            "present"
            if (artifacts / "brief.yaml").is_file()
            else "optional; a built-in default brief is used",
        )
    )
    checks.append(
        Check(
            "artifact:glossary",
            OK if (artifacts / "glossary.yaml").is_file() else INFO,
            "present"
            if (artifacts / "glossary.yaml").is_file()
            else "optional; glossary mode is off by default",
        )
    )
    chapters = 0
    try:
        connection = sqlite3.connect(db_path)
        try:
            book = connection.execute(
                "SELECT id FROM books WHERE slug = ? LIMIT 1", (slug,)
            ).fetchone()
            if book is not None:
                chapters = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM chapters WHERE book_id = ?", (int(book[0]),)
                    ).fetchone()[0]
                )
        finally:
            connection.close()
    except sqlite3.Error:
        chapters = 0
    anchors_present = (artifacts / "anchors.yaml").is_file()
    checks.append(
        Check(
            "artifact:anchors",
            OK if anchors_present else (WARN if chapters > 1 else INFO),
            "present"
            if anchors_present
            else "optional; chapters 2+ run with a warning unless anchors are approved",
        )
    )
    return checks


def _model_checks(settings: Settings, profile: ModelProfile) -> list[Check]:
    from shahrzad.llm.litellm_provider import LiteLLMProvider
    from shahrzad.llm.provider import Message

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
        except ShahrzadError as exc:
            checks.append(Check(f"model:{alias}", FAIL, f"{tier.model}: {exc.message}"))
    return checks


def _render(checks: list[Check], json_out: bool) -> bool:
    failed = any(check.status == FAIL for check in checks)
    if json_out:
        typer.echo(
            json.dumps(
                {"checks": [asdict(check) for check in checks], "failed": failed}, indent=2
            )
        )
        return failed
    table = Table(title="shz doctor", show_lines=False)
    table.add_column("check", style="bold")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    for check in checks:
        table.add_row(check.name, _SYMBOL.get(check.status, check.status), check.detail)
    console.print(table)
    if failed:
        console.print("[red]environment is not ready[/red]")
    return failed
