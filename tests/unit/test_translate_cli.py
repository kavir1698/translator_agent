from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tarjoman.cli.main import app

from .support import SmartProvider, count_versions, make_workspace

runner = CliRunner()


def _patch(monkeypatch, provider: SmartProvider) -> None:
    monkeypatch.setattr(
        "tarjoman.cli.translate_cmd.build_provider", lambda _context: provider
    )
    monkeypatch.setattr(
        "tarjoman.cli.retranslate_cmd.build_provider", lambda _context: provider
    )


def test_cli_translate_qa_and_retranslate(
    isolated_home: Path, fixtures: dict[str, Path], monkeypatch
) -> None:
    base = make_workspace(isolated_home, fixtures)
    provider = SmartProvider()
    _patch(monkeypatch, provider)
    result = runner.invoke(app, ["translate", "--chapters", "1", "--cost-mode", "standard"])
    assert result.exit_code == 0, result.stdout
    assert "coverage 100.0%" in result.stdout
    assert (base / "runs").is_dir()

    qa = runner.invoke(app, ["qa", "--json"])
    assert qa.exit_code == 0, qa.stdout
    payload = json.loads(qa.stdout.strip().splitlines()[-1])
    assert payload["flags"] >= 0
    assert (base / "review" / "flags.csv").is_file()

    before = count_versions(base / "book.db")
    segment_id = next(iter(before))
    retry = runner.invoke(app, ["retranslate", "--segments", segment_id])
    assert retry.exit_code == 0, retry.stdout
    after = count_versions(base / "book.db")
    assert after[segment_id] == before[segment_id] + 1


def test_cli_dry_run_and_budget_exit(
    isolated_home: Path, fixtures: dict[str, Path], monkeypatch
) -> None:
    make_workspace(isolated_home, fixtures)
    provider = SmartProvider()
    _patch(monkeypatch, provider)
    dry = runner.invoke(app, ["translate", "--chapters", "1", "--dry-run"])
    assert dry.exit_code == 0, dry.stdout
    assert provider.calls == []
    assert "dry run" in dry.stdout

    paused = runner.invoke(
        app, ["translate", "--chapters", "1", "--max-usd", "1e-9"]
    )
    assert paused.exit_code == 3, paused.stdout


def test_cli_anchors_after_calibration(
    isolated_home: Path, fixtures: dict[str, Path], monkeypatch
) -> None:
    base = make_workspace(isolated_home, fixtures)
    provider = SmartProvider()
    _patch(monkeypatch, provider)
    assert runner.invoke(app, ["translate", "--chapters", "1"]).exit_code == 0
    proposed = runner.invoke(app, ["anchors", "propose"])
    assert proposed.exit_code == 0, proposed.stdout
    approved = runner.invoke(app, ["anchors", "approve"])
    assert approved.exit_code == 0, approved.stdout
    assert (base / "artifacts" / "anchors.yaml").is_file()
    listed = runner.invoke(app, ["anchors", "list"])
    assert listed.exit_code == 0
    assert "approved" in listed.stdout
