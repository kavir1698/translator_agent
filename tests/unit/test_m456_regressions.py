"""Regression tests for the recently fixed M4-M6 behaviours."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from shahrzad.context.packer import ledger_slice
from shahrzad.core.config import load_settings
from shahrzad.core.cost import load_model_profile
from shahrzad.core.db import Database
from shahrzad.llm.provider import LLMResult
from shahrzad.pipeline.anchors_pass import propose_anchors
from shahrzad.pipeline.qa_pass import QaOptions, run_qa
from shahrzad.pipeline.translate_pass import TranslateOptions, run_translate
from shahrzad.pipeline.window import plan_windows

from .support import SmartProvider, make_workspace

_SEGMENT = re.compile(r'<segment id="([^"]+)">\s*(.*?)\s*</segment>', re.DOTALL)


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


def _open_db(base: Path) -> Database:
    db = Database(base / "book.db")
    db.initialize()
    return db


def _segment(idx: int, kind: str, tokens: int, *, status: str = "pending") -> dict:
    return {
        "id": f"c001.s{idx:04d}",
        "chapter_id": 1,
        "idx": idx,
        "kind": kind,
        "status": status,
        "tokens_est": tokens,
        "dialogue_ratio": 0.0,
        "source_text": f"Paragraph {idx} with several words in it.",
        "source_html": f"<p>Paragraph {idx}</p>",
        "nodes_json": "[]",
    }


def _ledger_row(kind: str, key: str, value: dict) -> dict:
    return {"kind": kind, "key": key, "value_json": json.dumps(value, ensure_ascii=False)}


class UnverifiedQuoteProvider(SmartProvider):
    """A fidelity critic whose only quote appears nowhere in the source."""

    def _fidelity(self, content: str, messages: list[Any]) -> LLMResult:
        matches = [match for match in _SEGMENT.finditer(content) if len(match.group(2).split()) >= 4]
        segment_id, source = ("", "")
        if matches:
            segment_id, source = matches[0].group(1), matches[0].group(2)
        assert source
        payload = {
            "issues": [
                {
                    "segment_id": segment_id,
                    "type": "omission",
                    "severity": "major",
                    "quote_source": "not present in the source at all",
                    "quote_target": "",
                    "problem": "invented omission",
                    "suggested_fix": "none",
                }
            ],
            "verdict": "major_issues",
            "segment_verdicts": {segment_id: "major"},
        }
        return LLMResult(
            text=json.dumps(payload, ensure_ascii=False),
            parsed=payload,
            finish_reason="stop",
        )


def test_upsert_issue_reopens_resolved_but_keeps_waived(tmp_path: Path) -> None:
    db = Database(tmp_path / "book.db")
    db.initialize()
    try:
        kwargs = {
            "stage": "qa",
            "code": "L08",
            "dedupe_key": "ch:1:L08:length",
            "severity": "warn",
            "message": "first pass",
        }
        issue_id = db.upsert_issue(**kwargs)
        db.set_issue_status(issue_id, "resolved")
        assert db.fetchone("SELECT status FROM issues WHERE id = ?", (issue_id,))["status"] == (
            "resolved"
        )

        reopened = db.upsert_issue(**{**kwargs, "severity": "error", "message": "second pass"})
        row = db.fetchone("SELECT * FROM issues WHERE id = ?", (reopened,))
        assert reopened == issue_id
        assert row["status"] == "open"
        assert row["severity"] == "error"
        assert row["message"] == "second pass"

        db.set_issue_status(issue_id, "waived", resolved_by="editor", resolution="intentional")
        again = db.upsert_issue(**{**kwargs, "message": "third pass"})
        row = db.fetchone("SELECT * FROM issues WHERE id = ?", (again,))
        assert again == issue_id
        assert row["status"] == "waived"
        assert row["resolved_by"] == "editor"
        assert row["resolution"] == "intentional"
        assert row["message"] == "third pass"
    finally:
        db.close()


def test_scoped_qa_leaves_chapter_two_issues_open(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    db = _open_db(base)
    try:
        chapter_two = db.fetchone("SELECT * FROM chapters WHERE idx = 2")
        assert chapter_two is not None
        issue_id = db.upsert_issue(
            stage="qa",
            code="L99_PLANTED",
            dedupe_key="ch:2:L99_PLANTED",
            severity="error",
            message="chapter two only",
            chapter_id=int(chapter_two["id"]),
            context={"planted": True},
        )
        before = dict(db.fetchone("SELECT * FROM issues WHERE id = ?", (issue_id,)))
    finally:
        db.close()

    run_qa(
        root=isolated_home,
        slug="test-book",
        settings=load_settings(root=isolated_home),
        options=QaOptions(chapter=1),
    )

    db = _open_db(base)
    try:
        after = dict(db.fetchone("SELECT * FROM issues WHERE id = ?", (issue_id,)))
    finally:
        db.close()
    assert after == before
    assert after["status"] == "open"
    assert after["message"] == "chapter two only"


def test_omission_revision_resolves_critique_issue(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    result = _translate(
        isolated_home, SmartProvider(mode="omission"), TranslateOptions(chapters=[1])
    )
    db = _open_db(base)
    try:
        revisions = db.scalar("SELECT COUNT(*) FROM translations WHERE pass = 'revision'")
        critique = db.fetchall("SELECT status FROM issues WHERE code = 'A6_omission'")
    finally:
        db.close()
    assert result.coverage == 1.0
    assert result.error_issues == 0
    assert revisions and int(revisions) > 0
    assert critique
    assert {str(row["status"]) for row in critique} == {"resolved"}


def test_missing_segment_recovers_on_second_run(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    db = _open_db(base)
    try:
        segment_id = str(
            db.fetchone(
                "SELECT s.id FROM segments s JOIN chapters c ON c.id = s.chapter_id "
                "WHERE c.idx = 1 AND s.kind = 'prose' ORDER BY s.idx LIMIT 1 OFFSET 1"
            )["id"]
        )
    finally:
        db.close()

    first = _translate(
        isolated_home,
        SmartProvider(fail_segment=segment_id),
        TranslateOptions(chapters=[1], cost_mode="economy", no_cache=True),
    )
    db = _open_db(base)
    try:
        missing = db.fetchone(
            "SELECT status FROM issues WHERE stage = 'translate' "
            "AND code = 'SEGMENT_MISSING' AND segment_id = ?",
            (segment_id,),
        )
        status = db.fetchone("SELECT status FROM segments WHERE id = ?", (segment_id,))["status"]
    finally:
        db.close()
    assert first.coverage < 1.0
    assert missing is not None and missing["status"] == "open"
    assert status == "failed"

    second = _translate(
        isolated_home,
        SmartProvider(),
        TranslateOptions(chapters=[1], cost_mode="economy", force=False, no_cache=True),
    )
    db = _open_db(base)
    try:
        resolved = db.fetchone(
            "SELECT status FROM issues WHERE stage = 'translate' "
            "AND code = 'SEGMENT_MISSING' AND segment_id = ?",
            (segment_id,),
        )
        status = db.fetchone("SELECT status FROM segments WHERE id = ?", (segment_id,))["status"]
    finally:
        db.close()
    assert second.coverage == 1.0
    assert second.error_issues == 0
    assert resolved is not None and resolved["status"] == "resolved"
    assert status == "done"


def test_ledger_slice_matches_key_or_english_name() -> None:
    rows = [
        _ledger_row("character", "santiago", {"en": "Santiago", "fa": "سانتیاگو"}),
        _ledger_row("character", "oldman", {"en": "The Old Man", "fa": "پیرمرد"}),
        _ledger_row("character", "perico", {"en": "Perico", "fa": "پریکو"}),
    ]

    by_key = ledger_slice("Santiago rowed the boat alone.", rows)
    assert "Santiago" in by_key

    by_english_name = ledger_slice("The Old Man slept in the shade.", rows)
    assert "The Old Man" in by_english_name

    assert ledger_slice("The village was quiet that morning.", rows) == ""


def test_window_planner_never_emits_heading_only_window() -> None:
    segments = [
        _segment(1, "heading", 10),
        _segment(2, "prose", 2000),
        _segment(3, "prose", 100),
    ]
    windows = plan_windows(
        segments, chapter_id=1, chapter_idx=1, max_paragraphs=8, max_source_tokens=900
    )
    assert windows
    first_kinds = [str(segment["kind"]) for segment in windows[0].segments]
    assert "heading" in first_kinds and "prose" in first_kinds
    assert all(
        not all(str(segment["kind"]) == "heading" for segment in window.segments)
        for window in windows
    )


def test_anchor_proposer_prefers_non_dialogue_narration(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    db = _open_db(base)
    try:
        book_id = int(db.fetchone("SELECT id FROM books LIMIT 1")["id"])
        chapter = db.fetchone(
            "SELECT * FROM chapters WHERE book_id = ? AND kind = 'body' ORDER BY idx LIMIT 1",
            (book_id,),
        )
        assert chapter is not None
        prose = db.fetchall(
            "SELECT * FROM segments WHERE chapter_id = ? AND kind = 'prose' ORDER BY idx",
            (int(chapter["id"]),),
        )
        assert len(prose) >= 2
        dialogue, narration = prose[0], prose[1]

        long_dialogue = " ".join(["Dialogue with many words."] * 12)
        short_narration = "A short narration line."
        assert len(long_dialogue) > len(short_narration)
        db.execute(
            "UPDATE segments SET status = 'done', dialogue_ratio = ?, source_text = ? WHERE id = ?",
            (0.9, long_dialogue, str(dialogue["id"])),
        )
        db.execute(
            "UPDATE segments SET status = 'done', dialogue_ratio = ?, source_text = ? WHERE id = ?",
            (0.0, short_narration, str(narration["id"])),
        )
        for segment in (dialogue, narration):
            db.add_translation(
                segment_id=str(segment["id"]),
                pass_kind="draft",
                fa_text="متن نمونه",
                fa_markup="متن نمونه",
                model="mock",
            )

        result = propose_anchors(
            root=isolated_home, slug="test-book", db=db, book_id=book_id
        )
    finally:
        db.close()

    assert result.anchors
    first = result.anchors[0]
    assert first["segment_id"] == str(narration["id"])
    assert first["note"].startswith("narration")


def test_unverified_critic_quote_does_not_trigger_revision(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    result = _translate(
        isolated_home, UnverifiedQuoteProvider(), TranslateOptions(chapters=[1])
    )
    db = _open_db(base)
    try:
        revisions = db.scalar("SELECT COUNT(*) FROM translations WHERE pass = 'revision'")
        critique = db.fetchone(
            "SELECT status, severity, context_json FROM issues WHERE code = 'A6_omission'"
        )
    finally:
        db.close()
    assert result.coverage == 1.0
    assert int(revisions or 0) == 0
    assert critique is not None
    assert critique["status"] == "open"
    assert critique["severity"] == "warn"
    context = json.loads(critique["context_json"])
    assert context["quote_verified"] is False
    assert context["unverified_quote"] is True
