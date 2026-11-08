from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from shahrzad.context.packer import PackState, build_pack
from shahrzad.core.config import load_settings
from shahrzad.core.cost import load_model_profile
from shahrzad.core.db import Database
from shahrzad.core.errors import ConfigError
from shahrzad.pipeline.anchors_pass import (
    approve_anchors,
    load_anchor_draft,
    load_approved_anchors,
    propose_anchors,
    reject_anchors,
)
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


def test_propose_approve_and_pack(isolated_home: Path, fixtures: dict[str, Path]) -> None:
    base = make_workspace(isolated_home, fixtures)
    _run(isolated_home, SmartProvider(), TranslateOptions(chapters=[1]))

    db = Database(base / "book.db")
    db.initialize()
    book_id = int(db.fetchone("SELECT id FROM books LIMIT 1")["id"])
    try:
        proposed = propose_anchors(
            root=isolated_home, slug="test-book", db=db, book_id=book_id
        )
        assert proposed.proposed >= 2
        draft = load_anchor_draft(base)
        assert draft is not None and len(draft["candidates"]) >= 2
        approved = approve_anchors(db=db, book_id=book_id)
        assert approved.approved >= 2
        assert (base / "artifacts" / "anchors.yaml").is_file()
        book = db.get_book("test-book")
        metadata = json.loads(book["metadata_json"])
        assert metadata["anchors_hash"].startswith("sha256:")
        anchors = load_approved_anchors(base)
        assert len(anchors) >= 2
        variables = build_pack(
            window_segments=[],
            todo_segments=[],
            state=PackState(anchors=tuple(anchors)),
        )
        assert "EN:" in variables["anchors"]
        assert "FA:" in variables["anchors"]
    finally:
        db.close()


def test_reject_candidates(isolated_home: Path, fixtures: dict[str, Path]) -> None:
    base = make_workspace(isolated_home, fixtures)
    _run(isolated_home, SmartProvider(), TranslateOptions(chapters=[1]))
    db = Database(base / "book.db")
    db.initialize()
    book_id = int(db.fetchone("SELECT id FROM books LIMIT 1")["id"])
    try:
        propose_anchors(root=isolated_home, slug="test-book", db=db, book_id=book_id)
        rejected = reject_anchors(db=db, book_id=book_id, ids=["a001"])
        assert rejected.rejected == 1
        draft = load_anchor_draft(base)
        assert draft is not None
        assert draft["candidates"][0]["status"] == "rejected"
    finally:
        db.close()


def test_require_anchors_blocks_chapter_two(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _run(isolated_home, SmartProvider(), TranslateOptions(chapters=[1]))
    with pytest.raises(ConfigError):
        _run(
            isolated_home,
            SmartProvider(),
            TranslateOptions(chapters=[2], require_anchors=True),
        )
    result = _run(
        isolated_home, SmartProvider(), TranslateOptions(chapters=[2], no_anchors=True)
    )
    assert result.coverage == 1.0


def test_calibrate_writes_draft_and_stops(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _run(isolated_home, SmartProvider(), TranslateOptions(chapters=[1]))
    before = sqlite3.connect(base / "book.db").execute(
        "SELECT COUNT(*) FROM translations"
    ).fetchone()[0]
    result = _run(isolated_home, SmartProvider(), TranslateOptions(calibrate=True))
    after = sqlite3.connect(base / "book.db").execute(
        "SELECT COUNT(*) FROM translations"
    ).fetchone()[0]
    assert before == after
    assert (base / "artifacts" / "anchors.draft.yaml").is_file()
    assert any("anchors" in warning for warning in result.warnings)
