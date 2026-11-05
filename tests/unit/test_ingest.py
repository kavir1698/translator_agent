from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

import pytest
from tests.fixtures.build import SAMPLE_PARAGRAPHS
from typer.testing import CliRunner

from tarjoman.cli.main import app
from tarjoman.core.config import load_settings
from tarjoman.core.db import Database
from tarjoman.core.errors import ConfigError, PdfNoTextLayer
from tarjoman.ingest.blocks import BlockKind
from tarjoman.ingest.normalize_source import normalized_for_compare
from tarjoman.ingest.reader import read_source
from tarjoman.pipeline.init_pass import init_book

runner = CliRunner()


def _assert_roundtrip(result) -> None:
    blocks = result.all_blocks()
    assert blocks
    for block in blocks:
        assert block.rebuild() == block.html, f"{block.kind}: {block.text[:60]}"


def test_epub_roundtrip(fixtures: dict[str, Path]):
    result = read_source(fixtures["epub"])
    assert result.title == "The Test Book"
    assert result.author == "A. Writer"
    assert len(result.documents) == 2
    _assert_roundtrip(result)
    kinds = {block.kind for block in result.all_blocks()}
    assert {BlockKind.HEADING, BlockKind.PROSE, BlockKind.VERSE, BlockKind.TABLE} <= kinds
    assert BlockKind.FOOTNOTE in kinds
    assert BlockKind.CAPTION in kinds


def test_docx_roundtrip(fixtures: dict[str, Path]):
    result = read_source(fixtures["docx"])
    assert result.title == "The Test Book"
    _assert_roundtrip(result)


def test_markdown_roundtrip(fixtures: dict[str, Path]):
    result = read_source(fixtures["txt"])
    _assert_roundtrip(result)
    kinds = {block.kind for block in result.all_blocks()}
    assert {BlockKind.HEADING, BlockKind.PROSE, BlockKind.QUOTE} <= kinds


def test_pdf_quality_and_text(fixtures: dict[str, Path]):
    result = read_source(fixtures["pdf"])
    assert result.import_quality is not None and result.import_quality >= 0.7
    _assert_roundtrip(result)
    pdf_texts = [
        block.text for block in result.all_blocks() if block.kind is BlockKind.PROSE
    ]
    docx_texts = [
        block.text
        for block in read_source(fixtures["docx"]).all_blocks()
        if block.kind is BlockKind.PROSE
    ]
    for sample in SAMPLE_PARAGRAPHS:
        normalized = normalized_for_compare(sample)
        assert any(normalized in normalized_for_compare(text) for text in pdf_texts)
        best = max(
            difflib.SequenceMatcher(None, normalized, normalized_for_compare(text)).ratio()
            for text in docx_texts
        )
        assert best >= 0.95


def test_image_only_pdf_fails(fixtures: dict[str, Path]):
    with pytest.raises(PdfNoTextLayer):
        read_source(fixtures["image_pdf"])


def test_init_and_counts(isolated_home: Path, fixtures: dict[str, Path]):
    settings = load_settings(root=isolated_home)
    result = init_book(root=isolated_home, source_path=fixtures["epub"], settings=settings)
    assert result.created
    assert result.chapters == 2
    assert result.segments >= 10
    assert result.words > 0
    book_dir = isolated_home / "books" / result.slug
    assert (book_dir / "book.db").is_file()
    assert (book_dir / "source" / "original.epub").is_file()
    assert (book_dir / "source" / "blocks.json").is_file()
    with Database(book_dir / "book.db") as db:
        book = db.get_book(result.slug)
        assert book is not None
        assert book["source_format"] == "epub"
        chapters = db.chapters(result.book_id)
        assert [chapter["idx"] for chapter in chapters] == [1, 2]
        segments = db.segments(book_id=result.book_id)
        assert len(segments) == result.segments
        assert {row["status"] for row in segments} == {"pending"}
        assert all(row["nodes_json"] for row in segments)
        footnote = next(row for row in segments if row["kind"] == "footnote")
        assert "Footnote" in footnote["source_text"]


def test_reinit_is_idempotent(isolated_home: Path, fixtures: dict[str, Path]):
    settings = load_settings(root=isolated_home)
    first = init_book(root=isolated_home, source_path=fixtures["epub"], settings=settings)
    second = init_book(root=isolated_home, source_path=fixtures["epub"], settings=settings)
    assert first.created and not second.created
    assert (first.chapters, first.segments) == (second.chapters, second.segments)
    with Database(isolated_home / "books" / first.slug / "book.db") as db:
        assert db.scalar("SELECT COUNT(*) FROM books") == 1


def test_different_file_needs_force(isolated_home: Path, fixtures: dict[str, Path]):
    settings = load_settings(root=isolated_home)
    first = init_book(root=isolated_home, source_path=fixtures["epub"], settings=settings)
    with pytest.raises(ConfigError):
        init_book(root=isolated_home, source_path=fixtures["docx"], settings=settings)
    forced = init_book(
        root=isolated_home, source_path=fixtures["docx"], settings=settings, force=True
    )
    assert forced.created
    assert forced.source_format == "docx"
    backups = list((isolated_home / "backups").glob(f"{first.slug}.bak-*"))
    assert backups


def test_structure_yaml_override(isolated_home: Path, fixtures: dict[str, Path]):
    settings = load_settings(root=isolated_home)
    slug = "structured"
    artifacts = isolated_home / "books" / slug / "artifacts"
    artifacts.mkdir(parents=True)
    shutil.copy2(fixtures["structure"], artifacts / "structure.yaml")
    result = init_book(
        root=isolated_home, source_path=fixtures["epub"], settings=settings, slug=slug
    )
    assert result.chapters >= 2


def test_cli_init_and_inspect(isolated_home: Path, fixtures: dict[str, Path]):
    init = runner.invoke(app, ["init", str(fixtures["epub"])])
    assert init.exit_code == 0, init.stdout
    assert "initialized" in init.stdout

    inspect = runner.invoke(app, ["inspect", "--json", "--diff"])
    assert inspect.exit_code == 0, inspect.stdout
    payload = json.loads(inspect.stdout)
    assert payload["chapters"] == 2
    assert payload["segments"] > 0
    assert payload["segments_by_status"] == {"pending": payload["segments"]}
    assert payload["diff"]["source_unchanged"] is True
