from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from lxml import html as lxml_html
from typer.testing import CliRunner

from tarjoman.cli.main import app
from tarjoman.core.config import api_key_for, load_settings
from tarjoman.core.db import Database
from tarjoman.core.errors import ConfigError
from tarjoman.core.paths import find_book_dir
from tarjoman.ingest.blocks import make_block
from tarjoman.ingest.chapterize import chapterize
from tarjoman.ingest.inline import extract_nodes, render_with_sentinels
from tarjoman.ingest.normalize_source import normalize_source
from tarjoman.ingest.reader import read_source
from tarjoman.persian.lint import LintIssue, LintSegment, LintTranslation, check_segment
from tarjoman.persian.markup import normalize_translation, rebuilt_html, strip_markup
from tarjoman.pipeline.init_pass import init_book
from tests.fixtures.build import SAMPLE_PARAGRAPHS

runner = CliRunner()


def _nodes(fragment: str):
    element = lxml_html.fragment_fromstring(fragment)
    return extract_nodes(element), element


def test_force_same_source_archives_and_rebuilds(isolated_home: Path, fixtures: dict[str, Path]):
    settings = load_settings(root=isolated_home)
    first = init_book(root=isolated_home, source_path=fixtures["epub"], settings=settings)
    forced = init_book(
        root=isolated_home, source_path=fixtures["epub"], settings=settings, force=True
    )
    assert forced.created
    assert (forced.chapters, forced.segments) == (first.chapters, first.segments)
    backups = list((isolated_home / "backups").glob(f"{first.slug}.bak-*"))
    assert len(backups) == 1
    assert (backups[0] / "book.db").is_file()
    assert (backups[0] / "source" / "blocks.json").is_file()


def test_env_off_is_a_string(repo_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TARJOMAN_GLOSSARY__MODE", "off")
    settings = load_settings(root=repo_root)
    assert settings.glossary.mode == "off"


def test_dotenv_is_loaded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    shutil.copytree(Path(__file__).resolve().parents[2] / "configs", tmp_path / "configs")
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=from-dotenv\n", encoding="utf-8")
    assert api_key_for("deepseek", tmp_path) == "from-dotenv"


def test_books_dir_setting_is_honoured(isolated_home: Path, fixtures: dict[str, Path]):
    settings = load_settings(
        root=isolated_home, overrides={"project": {"books_dir": "library"}}
    )
    result = init_book(root=isolated_home, source_path=fixtures["epub"], settings=settings)
    assert (isolated_home / "library" / result.slug / "book.db").is_file()
    assert find_book_dir(isolated_home, result.slug, "library").name == result.slug


def test_l10_whitelist_uses_token_boundaries():
    segment = LintSegment(id="c001.s0001", source_text="hello")
    issues = check_segment(segment, LintTranslation("I said look at that"))
    assert any(issue.code == "L10" for issue in issues)
    allowed = check_segment(segment, LintTranslation("AI ISBN 978-0-00-000000-0"))
    assert not any(issue.code == "L10" for issue in allowed)


def test_rebuilt_html_keeps_translated_plain_text():
    nodes, _ = _nodes("<p>He was <em>very</em> angry.</p>")
    html = rebuilt_html("او ⟨2⟩خیلی⟨/2⟩ عصبانی بود.", nodes)
    assert "او" in html and "عصبانی" in html
    assert "He was" not in html
    assert "<em>خیلی</em>" in html


def test_strip_markup_preserves_verse_newlines():
    nodes, _ = _nodes("<p>یک<br/>دو</p>")
    markup = render_with_sentinels(nodes)
    result = normalize_translation(markup, nodes)
    assert "\n" in result.fa_text
    assert strip_markup(markup, nodes).count("\n") == 1


def test_comments_are_not_content():
    block = make_block(lxml_html.fragment_fromstring("<p>a<!-- note -->b</p>"))
    assert block.text == "ab"
    assert "cyfunction" not in block.html
    assert block.rebuild() == block.html
    assert all("cyfunction" not in json.dumps(node) for node in block.nodes)


def test_hyphen_join_respects_lowercase():
    assert normalize_source("well-\nknown") == "wellknown"
    assert normalize_source("State-\nDepartment") == "State-\nDepartment"


def test_tiny_chapter_merges_forward(isolated_home: Path, fixtures: dict[str, Path]):
    def doc():
        fragments = [
            "<h1>Long A</h1>",
            f"<p>{SAMPLE_PARAGRAPHS[0]}</p>",
            "<h1>Short B</h1>",
            "<p>کوتاه</p>",
            "<h1>Long C</h1>",
            f"<p>{SAMPLE_PARAGRAPHS[1]}</p>",
        ]
        return lxml_html.fragment_fromstring("".join(fragments), create_parent="div")

    from tarjoman.ingest.types import Document, IngestResult

    wrapper = doc()
    blocks = [make_block(child) for child in wrapper]
    result = IngestResult(
        source_format="txt",
        documents=[Document(id="doc001", blocks=blocks)],
        title="test",
    )
    chapters = chapterize(result)
    assert len(chapters) == 2
    assert any("کوتاه" in block.text for block in chapters[1].blocks)


def test_empty_source_is_rejected(isolated_home: Path, tmp_path: Path):
    empty = tmp_path / "empty.txt"
    empty.write_text("   \n\n", encoding="utf-8")
    result = read_source(empty)
    assert result.all_blocks() == []
    settings = load_settings(root=isolated_home)
    with pytest.raises(ConfigError):
        init_book(root=isolated_home, source_path=empty, settings=settings)


def test_lint_dedupe_keys_are_database_compatible():
    issue = LintIssue("L01", "error", "missing")
    assert issue.dedupe_key("segment", "c001.s0001").startswith("seg:")
    assert issue.dedupe_key("window", "c001.w001").startswith("seg:")
    assert issue.dedupe_key("chapter", "c001").startswith("ch:")
    assert issue.dedupe_key("book", "1").startswith("ch:")


def test_run_heartbeat_and_stats_merge():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        database = Database(Path(tmp) / "book.db")
        database.initialize()
        book_id = database.insert_book(
            slug="b", title_en="B", source_path="x", source_sha256="a" * 64
        )
        run = database.start_run(book_id=book_id, stage="translate", config_json="{}")
        assert database.abort_orphan_runs(book_id) == 0
        database.update_run(run, stats={"heartbeat_at": "2000-01-01T00:00:00Z"})
        database.update_run(run, stats={"planned": 3})
        stats = json.loads(database.get_run(run)["stats_json"])
        assert stats["heartbeat_at"] == "2000-01-01T00:00:00Z"
        assert stats["planned"] == 3
        assert database.abort_orphan_runs(book_id) == 1
        database.close()


def test_cache_get_does_not_write():
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        database = Database(Path(tmp) / "book.db")
        database.initialize()
        database.cache_put("k", "{}")
        database.cache_get("k")
        assert database.scalar("SELECT last_hit_at FROM cache WHERE key = 'k'") is None
        database.close()


def test_markdown_links_are_escaped_and_scheme_checked():
    from tarjoman.ingest.txt_reader import md_inline

    assert md_inline("[x](javascript:evil)") == "x"
    rendered = md_inline('[x](http://example.com/" onmouseover="evil)')
    assert 'onmouseover="evil' not in rendered
    assert "&quot;" in rendered


def test_inspect_diff_uses_stored_path(isolated_home: Path, tmp_path: Path):
    source = tmp_path / "book.markdown"
    source.write_text("# Chapter One\n\nSome text for the book.\n", encoding="utf-8")
    init = runner.invoke(app, ["init", str(source)])
    assert init.exit_code == 0, init.stdout
    inspect = runner.invoke(app, ["--book", "chapter-one", "inspect", "--json", "--diff"])
    assert inspect.exit_code == 0, inspect.stdout
    payload = json.loads(inspect.stdout)
    assert payload["diff"]["stored_path"] == "source/original.markdown"
    assert payload["diff"]["source_unchanged"] is True
