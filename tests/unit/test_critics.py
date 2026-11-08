from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from shahrzad.core.config import load_settings
from shahrzad.core.cost import load_model_profile
from shahrzad.core.errors import BudgetPaused
from shahrzad.pipeline.translate_pass import TranslateOptions, run_translate

from .support import SmartProvider, make_workspace


def _run(home: Path, provider, options: TranslateOptions):
    return run_translate(
        root=home,
        slug="test-book",
        settings=load_settings(root=home),
        profile=load_model_profile(home / "configs/models/deepseek.yaml"),
        provider=provider,
        options=options,
    )


def _codes(base: Path) -> set[str]:
    connection = sqlite3.connect(base / "book.db")
    try:
        return {str(row[0]) for row in connection.execute("SELECT code FROM issues")}
    finally:
        connection.close()


def test_fidelity_omission_triggers_revision(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    provider = SmartProvider(mode="omission")
    result = _run(isolated_home, provider, TranslateOptions(chapters=[1], cost_mode="standard"))
    codes = _codes(base)
    assert "A6_omission" in codes
    connection = sqlite3.connect(base / "book.db")
    try:
        revisions = connection.execute(
            "SELECT COUNT(*) FROM translations WHERE pass = 'revision'"
        ).fetchone()[0]
        repairs = connection.execute(
            "SELECT COUNT(*) FROM translations WHERE pass = 'repair'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert revisions > 0 or repairs > 0
    assert result.coverage == 1.0


def test_bad_revision_is_rejected_by_regression_guard(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    provider = SmartProvider(mode="omission_bad")
    _run(isolated_home, provider, TranslateOptions(chapters=[1], cost_mode="standard"))
    codes = _codes(base)
    assert "revision_regressed" in codes
    connection = sqlite3.connect(base / "book.db")
    try:
        revisions = connection.execute(
            "SELECT COUNT(*) FROM translations WHERE pass = 'revision'"
        ).fetchone()[0]
        markup_issues = connection.execute(
            "SELECT COUNT(*) FROM issues WHERE code = 'L02' AND stage = 'qa'"
        ).fetchone()[0]
        total = connection.execute(
            "SELECT COUNT(*) FROM segments WHERE status != 'skipped'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert revisions < total
    assert markup_issues == 0


def test_budget_pause_is_recorded(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    with pytest.raises(BudgetPaused):
        _run(isolated_home, SmartProvider(), TranslateOptions(chapters=[1], max_usd=1e-9))
    connection = sqlite3.connect(base / "book.db")
    try:
        statuses = [row[0] for row in connection.execute("SELECT status FROM runs")]
    finally:
        connection.close()
    assert "paused" in statuses
