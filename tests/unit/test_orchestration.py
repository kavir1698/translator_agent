from __future__ import annotations

import sqlite3
from pathlib import Path

import yaml

from tarjoman.core.config import load_settings
from tarjoman.core.cost import load_model_profile
from tarjoman.core.db import Database
from tarjoman.pipeline.qa_pass import QaOptions, run_qa
from tarjoman.pipeline.translate_pass import TranslateOptions, run_translate

from .support import SmartProvider, count_versions, make_workspace


def _settings(home: Path):
    return load_settings(root=home, slug="test-book")


def _run(home: Path, provider, options: TranslateOptions):
    return run_translate(
        root=home,
        slug="test-book",
        settings=_settings(home),
        profile=load_model_profile(home / "configs/models/deepseek.yaml"),
        provider=provider,
        options=options,
    )


def _set_glossary_mode(base: Path, mode: str) -> None:
    path = base / "config.yaml"
    data = {}
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data.setdefault("glossary", {})
    data["glossary"]["mode"] = mode
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def test_interrupted_run_resumes_without_new_versions(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    provider = SmartProvider()
    first = _run(
        isolated_home, provider, TranslateOptions(chapters=[1], exit_after=1)
    )
    assert first.coverage < 1.0
    before = count_versions(base / "book.db")
    second = _run(isolated_home, provider, TranslateOptions(chapters=[1]))
    after = count_versions(base / "book.db")
    assert second.coverage == 1.0
    for segment_id, count in before.items():
        assert after[segment_id] == count
    assert second.report_path is not None and second.report_path.is_file()
    assert "Cost by agent" in second.report_path.read_text(encoding="utf-8")


def test_parallel_chapters(isolated_home: Path, fixtures: dict[str, Path]) -> None:
    base = make_workspace(isolated_home, fixtures)
    result = _run(
        isolated_home,
        SmartProvider(),
        TranslateOptions(parallel_chapters=2, cost_mode="standard"),
    )
    assert result.chapters >= 2
    assert result.coverage == 1.0
    assert any("parallel-chapter" in warning for warning in result.warnings)
    connection = sqlite3.connect(base / "book.db")
    try:
        statuses = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM segments WHERE status != 'skipped'"
            )
        ]
    finally:
        connection.close()
    assert set(statuses) == {"done"}


def test_one_chapter_failure_does_not_abort(
    isolated_home: Path, fixtures: dict[str, Path], monkeypatch
) -> None:
    make_workspace(isolated_home, fixtures)
    original = __import__(
        "tarjoman.pipeline.translate_pass", fromlist=["_process_window"]
    )._process_window

    def flaky(run, chapter, window, chapter_segments):
        if int(chapter["idx"]) == 1:
            raise RuntimeError("planted chapter failure")
        return original(run, chapter, window, chapter_segments)

    monkeypatch.setattr("tarjoman.pipeline.translate_pass._process_window", flaky)
    result = _run(isolated_home, SmartProvider(), TranslateOptions(chapters=[1, 2]))
    assert result.status == "completed"
    assert result.report_path is not None and result.report_path.is_file()
    connection = sqlite3.connect(isolated_home / "books/test-book/book.db")
    try:
        codes = {row[0] for row in connection.execute("SELECT code FROM issues")}
        chapter_two = connection.execute(
            "SELECT COUNT(*) FROM segments s JOIN chapters c ON c.id = s.chapter_id "
            "WHERE c.idx = 2 AND s.status = 'done'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert "WINDOW_FAILED" in codes
    assert chapter_two > 0


def test_ledger_and_chapter_close(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _run(isolated_home, SmartProvider(), TranslateOptions(chapters=[1]))
    assert (base / "artifacts" / "ledger.jsonl").is_file()
    connection = sqlite3.connect(base / "book.db")
    try:
        chapter = connection.execute(
            "SELECT status, title_fa, rolling_summary_fa FROM chapters WHERE idx = 1"
        ).fetchone()
    finally:
        connection.close()
    assert chapter[0] == "translated"
    assert chapter[1]
    assert chapter[2]


def test_glossary_staleness_detects_and_retranslates(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _set_glossary_mode(base, "auto")
    db = Database(base / "book.db")
    db.initialize()
    db.upsert_glossary_entry(
        book_id=1,
        term_en="harbour",
        term_fa="بندر",
        kind="term",
        status="approved",
        source="imported",
    )
    db.close()
    provider = SmartProvider()
    _run(isolated_home, provider, TranslateOptions(chapters=[1]))
    qa = run_qa(
        root=isolated_home,
        slug="test-book",
        settings=_settings(isolated_home),
        options=QaOptions(stale_glossary=True),
    )
    assert qa.stale == 0

    db = Database(base / "book.db")
    db.initialize()
    db.upsert_glossary_entry(
        book_id=1,
        term_en="Chapter One",
        term_fa="فصل یک",
        kind="term",
        status="approved",
        source="imported",
    )
    db.close()
    stale_qa = run_qa(
        root=isolated_home,
        slug="test-book",
        settings=_settings(isolated_home),
        options=QaOptions(stale_glossary=True),
    )
    assert stale_qa.stale > 0
    before = count_versions(base / "book.db")
    _run(
        isolated_home,
        provider,
        TranslateOptions(chapters=[1], only_stale=True),
    )
    after = count_versions(base / "book.db")
    assert sum(after.values()) > sum(before.values())
