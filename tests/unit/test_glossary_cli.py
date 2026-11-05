from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from tarjoman.cli.main import app
from tarjoman.core.config import load_settings
from tarjoman.core.db import Database
from tarjoman.pipeline.init_pass import init_book

runner = CliRunner()


def _init(isolated_home: Path, fixtures: dict[str, Path]) -> Path:
    init_book(
        root=isolated_home,
        source_path=fixtures["epub"],
        settings=load_settings(root=isolated_home),
        slug="test-book",
    )
    return isolated_home / "books" / "test-book"


def test_glossary_commands_workflow(isolated_home: Path, fixtures: dict[str, Path], tmp_path):
    base = _init(isolated_home, fixtures)
    with Database(base / "book.db") as db:
        book = db.get_book("test-book")
        assert book is not None
        entry_id = db.upsert_glossary_entry(
            book_id=int(book["id"]),
            term_en="Santiago",
            term_fa="سانتیاگو",
            kind="character",
            status="proposed",
            confidence=0.72,
        )

    reviewed = runner.invoke(app, ["glossary", "review", "--json"])
    assert reviewed.exit_code == 0, reviewed.stdout
    assert json.loads(reviewed.stdout)[0]["id"] == entry_id

    approved = runner.invoke(app, ["glossary", "approve", str(entry_id)])
    assert approved.exit_code == 0, approved.stdout

    listed = runner.invoke(app, ["glossary", "list", "--json"])
    assert listed.exit_code == 0, listed.stdout
    entries = json.loads(listed.stdout)
    assert entries[0]["status"] == "approved"

    edited = runner.invoke(app, ["glossary", "edit", str(entry_id), "--fa", "سانتیاگو"])
    assert edited.exit_code == 0, edited.stdout

    rejected = runner.invoke(app, ["glossary", "reject", str(entry_id)])
    assert rejected.exit_code == 0, rejected.stdout

    added = runner.invoke(
        app,
        ["glossary", "add", "--en", "marlin", "--fa", "مارلین", "--kind", "term"],
    )
    assert added.exit_code == 0, added.stdout

    exported = runner.invoke(app, ["glossary", "export"])
    assert exported.exit_code == 0, exported.stdout
    assert (base / "artifacts" / "glossary.yaml").is_file()

    terms = tmp_path / "terms.csv"
    terms.write_text("en,fa,kind\nManolin,مانولین,character\n", encoding="utf-8")
    imported = runner.invoke(app, ["glossary", "import", str(terms)])
    assert imported.exit_code == 0, imported.stdout

    synced = runner.invoke(app, ["glossary", "sync"])
    assert synced.exit_code == 0, synced.stdout

    with Database(base / "book.db") as db:
        book = db.get_book("test-book")
        assert book is not None
        db.add_glossary_suggestion(
            book_id=int(book["id"]),
            segment_id="c001.s0001",
            term_en="skiff",
            term_fa="قایق",
            kind="term",
            reason="recurring object",
        )
    pending = runner.invoke(app, ["glossary", "pending", "--json"])
    assert pending.exit_code == 0, pending.stdout
    suggestions = json.loads(pending.stdout)
    assert suggestions[0]["en"] == "skiff"
