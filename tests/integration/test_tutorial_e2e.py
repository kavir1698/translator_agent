"""Acceptance test for the documented offline tutorial (EPUB and text-layer PDF)."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tarjoman.cli.main import app
from tarjoman.core.config import load_settings
from tarjoman.core.cost import load_model_profile
from tarjoman.export.epub_writer import validate_epub
from tarjoman.pipeline.export_pass import ExportOptions, run_export
from tarjoman.pipeline.translate_pass import TranslateOptions, run_translate
from tests.unit.support import SmartProvider

runner = CliRunner()
PERSIAN = re.compile(r"[\u0600-\u06FF]{2,}")


def _init(home: Path, source: Path, slug: str) -> Path:
    result = runner.invoke(app, ["init", str(source), "--slug", slug])
    assert result.exit_code == 0, result.output
    return home / "books" / slug


def _translate(home: Path, slug: str):
    result = run_translate(
        root=home,
        slug=slug,
        settings=load_settings(root=home, slug=slug),
        profile=load_model_profile(home / "configs/models/deepseek.yaml"),
        provider=SmartProvider(),
        options=TranslateOptions(cost_mode="standard"),
    )
    assert result.coverage == 1.0, result.warnings
    assert result.error_issues == 0, result.warnings
    return result


def _export(home: Path, slug: str):
    return run_export(
        root=home,
        slug=slug,
        settings=load_settings(root=home, slug=slug),
        options=ExportOptions(formats=["epub", "docx", "md", "txt"]),
    )


@pytest.mark.parametrize(
    ("source", "slug"),
    [("epub", "tutorial-epub"), ("pdf", "tutorial-pdf")],
)
def test_documented_offline_path_for_epub_and_pdf(
    isolated_home: Path, fixtures: dict[str, Path], source: str, slug: str
) -> None:
    base = _init(isolated_home, fixtures[source], slug)

    if source == "pdf":
        connection = sqlite3.connect(base / "book.db")
        try:
            quality = connection.execute("SELECT import_quality FROM books").fetchone()[0]
        finally:
            connection.close()
        assert quality is not None and float(quality) >= 0.6

    _translate(isolated_home, slug)
    result = _export(isolated_home, slug)

    assert set(result.paths) == {"epub", "docx", "md", "txt"}
    assert result.incomplete is False
    for path in result.paths.values():
        assert path.is_file(), path

    assert result.paths["epub"].read_bytes().startswith(b"PK")
    assert result.paths["docx"].read_bytes().startswith(b"PK")
    markdown = result.paths["md"].read_text(encoding="utf-8")
    assert PERSIAN.search(markdown), "Markdown has no Persian chapter content"
    assert validate_epub(result.paths["epub"]) == []


def test_export_of_untranslated_workspace_exits_four(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    slug = "incomplete"
    _init(isolated_home, fixtures["epub"], slug)
    result = runner.invoke(app, ["--book", slug, "export", "--formats", "epub"])
    assert result.exit_code == 4, result.output
    assert "--allow-incomplete" in result.output


def test_doctor_exits_zero_without_keys(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.stdout)
    assert payload["failed"] is False
    checks = {check["name"]: check for check in payload["checks"]}
    assert checks["package"]["detail"].startswith("tarjoman 1.0.0")
    assert checks["books_dir"]["status"] == "ok"
