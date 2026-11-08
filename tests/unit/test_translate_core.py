from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tarjoman.core.config import load_settings
from tarjoman.core.cost import load_model_profile
from tarjoman.core.errors import BudgetPaused
from tarjoman.pipeline.qa_pass import run_qa
from tarjoman.pipeline.translate_pass import TranslateOptions, run_translate

from .support import SmartProvider, count_versions, make_workspace


def _profile(home: Path):
    return load_model_profile(home / "configs/models/deepseek.yaml")


def _translate(home: Path, provider, options: TranslateOptions):
    return run_translate(
        root=home,
        slug="test-book",
        settings=load_settings(root=home),
        profile=_profile(home),
        provider=provider,
        options=options,
    )


def _db(base: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(base / "book.db")
    connection.row_factory = sqlite3.Row
    return connection


def test_zero_config_chapter_one_and_rerun_cache(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    provider = SmartProvider()
    first = _translate(
        isolated_home, provider, TranslateOptions(chapters=[1], cost_mode="standard")
    )
    assert first.coverage == 1.0, first.warnings
    assert first.error_issues == 0, first.report_path.read_text(encoding="utf-8")
    assert first.report_path is not None and first.report_path.is_file()

    connection = _db(base)
    try:
        segments = connection.execute(
            "SELECT s.*, t.fa_markup FROM segments s JOIN chapters c ON c.id = s.chapter_id "
            "LEFT JOIN translations t ON t.segment_id = s.id AND t.is_current = 1 "
            "WHERE c.idx = 1 AND s.status != 'skipped'"
        ).fetchall()
    finally:
        connection.close()
    assert segments
    assert all(row["fa_markup"] for row in segments)
    assert all(row["status"] == "done" for row in segments)
    # Sentinels survive for the marked paragraph.
    marked = [row for row in segments if row["nodes_json"] not in ("[]", "")]
    assert marked
    assert any("⟨" in row["fa_markup"] for row in marked)

    before = count_versions(base / "book.db")
    second = _translate(
        isolated_home, provider, TranslateOptions(chapters=[1], cost_mode="standard")
    )
    after = count_versions(base / "book.db")
    assert before == after
    assert second.cost_usd == 0.0
    assert second.cache_hit_rate >= 0.5


def test_dry_run_makes_zero_calls(isolated_home: Path, fixtures: dict[str, Path]) -> None:
    make_workspace(isolated_home, fixtures)
    provider = SmartProvider()
    result = _translate(
        isolated_home, provider, TranslateOptions(chapters=[1], dry_run=True)
    )
    assert result.dry_run
    assert provider.calls == []
    assert result.estimate["calls"] > 0


def test_progress_callback_reports_live_events(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    events = []
    result = run_translate(
        root=isolated_home,
        slug="test-book",
        settings=load_settings(root=isolated_home),
        profile=_profile(isolated_home),
        provider=SmartProvider(),
        options=TranslateOptions(chapters=[1], cost_mode="standard"),
        progress=events.append,
    )
    assert result.coverage == 1.0
    kinds = [event.kind for event in events]
    assert kinds[0] == "start"
    assert "window_done" in kinds
    assert "chapter_done" in kinds
    assert kinds[-1] == "finish"
    assert events[0].chapters_total == 1
    assert events[0].windows_total > 0
    assert events[-1].status == "completed"
    window_done = next(event for event in events if event.kind == "window_done")
    assert window_done.windows_done >= 1
    assert window_done.chapter_windows >= 1


def test_broken_progress_callback_does_not_abort_translation(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)

    def broken(_event: object) -> None:
        raise RuntimeError("display exploded")

    result = run_translate(
        root=isolated_home,
        slug="test-book",
        settings=load_settings(root=isolated_home),
        profile=_profile(isolated_home),
        provider=SmartProvider(),
        options=TranslateOptions(chapters=[1], cost_mode="standard"),
        progress=broken,
    )
    assert result.coverage == 1.0
    assert result.error_issues == 0


def test_qa_writes_flags_and_is_idempotent(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate(isolated_home, SmartProvider(), TranslateOptions(chapters=[1]))
    settings = load_settings(root=isolated_home)
    first = run_qa(root=isolated_home, slug="test-book", settings=settings)
    assert first.flags_path is not None and first.flags_path.is_file()
    lines = first.flags_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == (
        "segment_id,chapter,risk_score,codes,severities,source_excerpt,fa_excerpt,"
        "sample_flag,reviewed,reviewer_note"
    )
    with _db(base) as connection:
        issues_first = connection.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    second = run_qa(root=isolated_home, slug="test-book", settings=settings)
    with _db(base) as connection:
        issues_second = connection.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
    assert second.flags == first.flags
    assert issues_second <= issues_first


def test_missing_segment_marks_failed_without_aborting(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    with _db(base) as connection:
        segment_id = connection.execute(
            "SELECT s.id FROM segments s JOIN chapters c ON c.id = s.chapter_id "
            "WHERE c.idx = 1 AND s.kind = 'prose' ORDER BY s.idx LIMIT 1 OFFSET 1"
        ).fetchone()[0]
    provider = SmartProvider(fail_segment=str(segment_id))
    result = _translate(
        isolated_home, provider, TranslateOptions(chapters=[1], cost_mode="economy")
    )
    assert result.coverage < 1.0
    with _db(base) as connection:
        status = connection.execute(
            "SELECT status FROM segments WHERE id = ?", (segment_id,)
        ).fetchone()[0]
        codes = {
            row[0]
            for row in connection.execute("SELECT code FROM issues WHERE segment_id = ?", (segment_id,))
        }
    assert status == "failed"
    assert "SEGMENT_MISSING" in codes


def test_budget_cap_pauses(isolated_home: Path, fixtures: dict[str, Path]) -> None:
    make_workspace(isolated_home, fixtures)
    with pytest.raises(BudgetPaused):
        _translate(
            isolated_home,
            SmartProvider(),
            TranslateOptions(chapters=[1], max_usd=1e-9),
        )
