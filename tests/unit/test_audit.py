from __future__ import annotations

import sqlite3
from pathlib import Path

from typer.testing import CliRunner

from tarjoman.cli.main import app
from tarjoman.core.config import load_settings
from tarjoman.core.cost import load_model_profile
from tarjoman.core.db import Database
from tarjoman.pipeline.audit_pass import AuditOptions, run_audit
from tarjoman.pipeline.translate_pass import TranslateOptions, run_translate

from .support import SmartProvider, count_versions, make_workspace

runner = CliRunner()


def _settings(home: Path):
    return load_settings(root=home, slug="test-book")


def _profile(home: Path):
    return load_model_profile(home / "configs/models/deepseek.yaml")


def _translate(home: Path, *, chapters: list[int] | None = None, provider=None):
    return run_translate(
        root=home,
        slug="test-book",
        settings=_settings(home),
        profile=_profile(home),
        provider=provider or SmartProvider(),
        options=TranslateOptions(chapters=chapters, cost_mode="standard"),
    )


def _audit(home: Path, provider, options: AuditOptions):
    return run_audit(
        root=home,
        slug="test-book",
        settings=_settings(home),
        profile=_profile(home),
        provider=provider,
        options=options,
    )


def _db(base: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(base / "book.db")
    connection.row_factory = sqlite3.Row
    return connection


def _first_translated_segment(base: Path, chapter_idx: int) -> sqlite3.Row:
    with _db(base) as connection:
        row = connection.execute(
            """
            SELECT s.id, s.chapter_id, t.fa_text, t.fa_markup, t.version
            FROM segments s
            JOIN chapters c ON c.id = s.chapter_id
            JOIN translations t ON t.segment_id = s.id AND t.is_current = 1
            WHERE c.idx = ? AND s.kind != 'heading'
            ORDER BY s.idx LIMIT 1
            """,
            (chapter_idx,),
        ).fetchone()
    assert row is not None
    return row


def _longest_translated_segment(base: Path, chapter_idx: int) -> sqlite3.Row:
    with _db(base) as connection:
        row = connection.execute(
            """
            SELECT s.id, s.chapter_id, t.fa_text, t.fa_markup, t.version
            FROM segments s
            JOIN chapters c ON c.id = s.chapter_id
            JOIN translations t ON t.segment_id = s.id AND t.is_current = 1
            WHERE c.idx = ? AND s.kind = 'prose'
            ORDER BY LENGTH(s.source_text) DESC LIMIT 1
            """,
            (chapter_idx,),
        ).fetchone()
    assert row is not None
    return row


def _append_translation(base: Path, segment_id: str, suffix: str) -> None:
    with _db(base) as connection:
        connection.execute(
            "UPDATE translations SET fa_text = fa_text || ? "
            "WHERE segment_id = ? AND is_current = 1",
            (suffix, segment_id),
        )
        connection.commit()


def test_deterministic_name_variant_across_chapters(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    assert _translate(isolated_home).coverage == 1.0
    first = _first_translated_segment(base, 1)
    second = _first_translated_segment(base, 2)
    _append_translation(base, str(first["id"]), " سانتیاگو")
    _append_translation(base, str(second["id"]), " سانتیاگ")

    db = Database(base / "book.db")
    db.initialize()
    db.upsert_glossary_entry(
        book_id=1,
        term_en="Santiago",
        term_fa="سانتیاگو",
        kind="character",
        status="approved",
        source="imported",
    )
    db.close()

    result = _audit(isolated_home, SmartProvider(), AuditOptions(readthrough=False, sample=0))
    assert result.chapters == 2

    with _db(base) as connection:
        rows = connection.execute(
            """
            SELECT code, severity, segment_id, status FROM issues
            WHERE stage = 'audit' AND code IN ('NAME_VARIANT','GLOSSARY_VARIANT')
            """
        ).fetchall()
    assert rows
    matching = [row for row in rows if row["segment_id"] == str(second["id"])]
    assert matching
    assert any(row["severity"] == "error" for row in matching)
    assert any(row["code"] in {"NAME_VARIANT", "GLOSSARY_VARIANT"} for row in matching)


def test_a10_findings_persist_and_chapter_becomes_audited(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate(isolated_home, chapters=[1])
    segment = _first_translated_segment(base, 1)
    provider = SmartProvider(
        audit_findings=[
            {
                "kind": "character",
                "severity": "major",
                "segment_id": str(segment["id"]),
                "evidence": "شاهد",
                "conflicts_with": "ledger.character",
                "explanation": "the character's title contradicts the ledger",
                "suggested_resolution": "use the ledger title",
            }
        ]
    )

    _audit(isolated_home, provider, AuditOptions(readthrough=False, sample=3))

    with _db(base) as connection:
        issue = connection.execute(
            "SELECT * FROM issues WHERE stage = 'audit' AND code = 'A10_CHARACTER'"
        ).fetchone()
        status = connection.execute("SELECT status FROM chapters WHERE idx = 1").fetchone()[0]
    assert issue is not None
    assert issue["severity"] == "error"
    assert issue["segment_id"] == str(segment["id"])
    assert "ledger.character" in issue["context_json"]
    assert status == "audited"


def test_readthrough_is_capped_and_never_rewrites(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate(isolated_home, chapters=[1])
    before = count_versions(base / "book.db")
    problems = ["rhythm", "translationese", "register", "dialogue", "clarity"]
    provider = SmartProvider(
        readthrough_issues=[
            {
                "passage": f"متن نمونه شماره {index}",
                "problem": problem,
                "why_it_hurts": "the rhythm drags",
                "suggested_rewrite": "کوتاه‌تر بنویس",
                "severity": "minor" if index % 2 else "major",
            }
            for index, problem in enumerate(problems)
        ]
    )

    _audit(isolated_home, provider, AuditOptions(readthrough=True, sample=0))

    after = count_versions(base / "book.db")
    assert after == before
    with _db(base) as connection:
        counts = connection.execute(
            "SELECT chapter_id, COUNT(*) FROM issues WHERE stage = 'readthrough' "
            "GROUP BY chapter_id"
        ).fetchall()
    assert counts
    assert max(int(row[1]) for row in counts) <= 5
    assert sum(int(row[1]) for row in counts) == 5


def test_audit_repair_creates_version_and_is_reported(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate(isolated_home, chapters=[1])
    segment = _longest_translated_segment(base, 1)
    before = count_versions(base / "book.db")
    provider = SmartProvider(
        mode="omission",
        audit_findings=[
            {
                "kind": "terminology",
                "severity": "major",
                "segment_id": str(segment["id"]),
                "evidence": "واژه",
                "explanation": "the approved term was paraphrased",
                "suggested_resolution": "restore the approved term",
            }
        ],
    )

    result = _audit(
        isolated_home,
        provider,
        AuditOptions(chapter=1, readthrough=False, sample=3),
    )

    after = count_versions(base / "book.db")
    assert after[str(segment["id"])] == before[str(segment["id"])] + 1
    assert result.repairs == 1
    with _db(base) as connection:
        repair = connection.execute(
            "SELECT * FROM translations WHERE segment_id = ? AND pass = 'repair'",
            (str(segment["id"]),),
        ).fetchone()
    assert repair is not None
    assert repair["is_current"] == 1
    assert result.report_path is not None
    report = result.report_path.read_text(encoding="utf-8")
    assert "## Repairs" in report
    assert str(segment["id"]) in report


def test_cli_audit_without_translations_is_a_config_error(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)

    result = runner.invoke(app, ["audit", "--chapter", "1", "--no-readthrough"])
    assert result.exit_code == 2, result.output
    assert "tj translate" in result.output
