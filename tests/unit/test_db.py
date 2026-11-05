from __future__ import annotations

import json
from pathlib import Path

import pytest

from tarjoman.core.db import Database
from tarjoman.core.errors import InvalidTransition, RuntimeFailure


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "book.db")
    database.initialize()
    yield database
    database.close()


def _book(database: Database, slug: str = "test-book") -> int:
    return database.insert_book(
        slug=slug,
        title_en="Test Book",
        source_path="source/original.epub",
        source_sha256="a" * 64,
        source_format="epub",
    )


def _segment(database: Database, book_id: int, chapter_idx: int = 1, seg_idx: int = 1) -> tuple[int, str]:
    chapter_id = database.insert_chapter(book_id=book_id, idx=chapter_idx, kind="body", title_en="One")
    sid = f"c{chapter_idx:03d}.s{seg_idx:04d}"
    database.insert_segment(
        segment_id=sid,
        chapter_id=chapter_id,
        idx=seg_idx,
        kind="prose",
        source_text="He was an old man.",
        source_html="<p>He was an old man.</p>",
        content_hash="b" * 64,
    )
    return chapter_id, sid


def test_schema_bootstrap(db: Database):
    assert db.schema_version() == 1
    tables = {
        row["name"]
        for row in db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"books", "chapters", "segments", "translations", "issues", "runs", "cache"} <= tables


def test_book_and_counts(db: Database):
    book_id = _book(db)
    _segment(db, book_id)
    counts = db.counts(book_id)
    assert counts == {"chapters": 1, "segments": 1, "translations": 0}
    assert db.get_book("test-book")["title_en"] == "Test Book"


def test_translation_versions_flip_current(db: Database):
    book_id = _book(db)
    _, sid = _segment(db, book_id)
    db.add_translation(
        segment_id=sid,
        pass_kind="draft",
        fa_text="پیرمردی بود.",
        fa_markup="پیرمردی بود.",
        model="mock",
    )
    db.add_translation(
        segment_id=sid,
        pass_kind="revision",
        fa_text="پیرمردی بود که تنها بود.",
        fa_markup="پیرمردی بود که تنها بود.",
        model="mock",
    )
    versions = db.translation_versions(sid)
    assert [row["version"] for row in versions] == [1, 2]
    assert [row["is_current"] for row in versions] == [0, 1]
    assert db.current_translation(sid)["fa_text"] == "پیرمردی بود که تنها بود."


def test_segment_transition_rules(db: Database):
    book_id = _book(db)
    _, sid = _segment(db, book_id)
    db.transition_segment(sid, "drafted")
    db.transition_segment(sid, "revised")
    db.transition_segment(sid, "done")
    db.transition_segment(sid, "flagged")
    with pytest.raises(InvalidTransition):
        db.transition_segment(sid, "skipped")  # flagged -> skipped is not a valid edge


def test_issue_dedupe_and_waiver(db: Database):
    book_id = _book(db)
    _, sid = _segment(db, book_id)
    kwargs = {
        "stage": "qa",
        "code": "L08",
        "dedupe_key": f"seg:{sid}:length",
        "severity": "error",
        "message": "length outlier",
        "segment_id": sid,
    }
    first = db.upsert_issue(**kwargs)
    second = db.upsert_issue(**kwargs)
    assert first == second
    assert len(db.issues_for(segment_id=sid)) == 1
    with pytest.raises(RuntimeFailure):
        db.upsert_issue(**{**kwargs, "dedupe_key": "unnamespaced"})
    with pytest.raises(RuntimeFailure):
        db.set_issue_status(first, "waived", resolved_by=None, resolution=None)
    db.set_issue_status(first, "waived", resolved_by="editor", resolution="intentional")
    assert db.issues_for(segment_id=sid)[0]["status"] == "waived"


def test_run_lifecycle_and_orphans(db: Database):
    book_id = _book(db)
    run = db.start_run(book_id=book_id, stage="translate", config_json="{}")
    db.transition_run(run, "paused")
    db.transition_run(run, "running")
    db.update_run(run, stats={"heartbeat_at": "2000-01-01T00:00:00Z"})
    assert db.abort_orphan_runs(book_id) == 1
    assert db.get_run(run)["status"] == "aborted"


def test_cache_roundtrip(db: Database):
    db.cache_put("k", json.dumps({"text": "hi"}))
    assert json.loads(db.cache_get("k") or "{}") == {"text": "hi"}
    assert db.cache_get("missing") is None
