from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest
from lxml import etree
from typer.testing import CliRunner

from tarjoman.cli.main import app
from tarjoman.core.config import load_settings
from tarjoman.core.cost import load_model_profile
from tarjoman.core.db import Database
from tarjoman.core.errors import RuntimeFailure
from tarjoman.export.epub_writer import validate_epub
from tarjoman.export.xhtml import isolate_latin
from tarjoman.pipeline.export_pass import ExportOptions, run_export
from tarjoman.pipeline.translate_pass import TranslateOptions, run_translate

from .support import SmartProvider, make_workspace

runner = CliRunner()

_OPF = "{http://www.idpf.org/2007/opf}"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _translate_all(home: Path) -> None:
    profile = load_model_profile(home / "configs/models/deepseek.yaml")
    result = run_translate(
        root=home,
        slug="test-book",
        settings=load_settings(root=home),
        profile=profile,
        provider=SmartProvider(),
        options=TranslateOptions(cost_mode="standard"),
    )
    assert result.coverage == 1.0, result.warnings
    assert result.error_issues == 0


def _export(home: Path, **overrides):
    options = ExportOptions(formats=["epub", "md", "txt"])
    for key, value in overrides.items():
        setattr(options, key, value)
    return run_export(
        root=home,
        slug="test-book",
        settings=load_settings(root=home),
        options=options,
    )


def _manifest_items(opf: etree._Element) -> list[etree._Element]:
    return list(opf.xpath("//*[local-name()='manifest']/*[local-name()='item']"))


def _resolved(opf_path: str, href: str) -> str:
    import posixpath

    return posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href))


def test_export_epub_md_txt_structure(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    result = _export(isolated_home)
    assert set(result.paths) == {"epub", "md", "txt"}
    assert not result.incomplete
    for path in result.paths.values():
        assert path.is_file()

    with zipfile.ZipFile(result.paths["epub"]) as archive:
        names = archive.namelist()
        assert names[0] == "mimetype"
        info = archive.getinfo("mimetype")
        assert info.compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"

        opf = etree.fromstring(archive.read("OEBPS/content.opf"))
        spine = opf.find(f"{_OPF}spine")
        assert spine is not None
        assert spine.get("page-progression-direction") == "rtl"
        assert opf.xpath("//*[local-name()='language']")[0].text == "fa"

        properties = {
            str(meta.get("property"))
            for meta in opf.xpath("//*[local-name()='meta']")
            if meta.get("property")
        }
        assert {
            "dcterms:modified",
            "schema:accessMode",
            "schema:accessModeSufficient",
            "schema:accessibilityFeature",
            "schema:accessibilityHazard",
            "schema:accessibilitySummary",
            "dcterms:conformsTo",
        } <= properties

        items = _manifest_items(opf)
        item_properties = {str(item.get("properties", "")) for item in items}
        assert "nav" in item_properties
        assert "cover-image" in item_properties
        media_types = {str(item.get("media-type")) for item in items}
        assert "font/woff2" in media_types
        assert "application/xhtml+xml" in media_types

        for item in items:
            href = str(item.get("href"))
            assert _resolved("OEBPS/content.opf", href) in names

        xhtml_items = [
            item for item in items if item.get("media-type") == "application/xhtml+xml"
        ]
        assert xhtml_items
        for item in xhtml_items:
            path = _resolved("OEBPS/content.opf", str(item.get("href")))
            data = archive.read(path)
            root = etree.fromstring(data)
            assert root.get("lang") == "fa", path
            assert root.get(_XML_LANG) == "fa", path
            assert root.get("dir") == "rtl", path
            assert b"<script" not in data.lower(), path
            assert not re.search(rb'(?:src|href)="(?:https?:)?//', data.lower()), path

    assert validate_epub(result.paths["epub"]) == []


def test_export_is_byte_deterministic(isolated_home: Path, fixtures: dict[str, Path]) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    first = _export(isolated_home)
    before = {name: path.read_bytes() for name, path in first.paths.items()}
    second = _export(isolated_home)
    after = {name: path.read_bytes() for name, path in second.paths.items()}
    for name in ("epub", "md", "txt"):
        assert before[name] == after[name], name


def test_markdown_and_plain_text_content(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    result = _export(isolated_home)
    markdown = result.paths["md"].read_text(encoding="utf-8")
    assert markdown.startswith("---\n")
    assert "\nlang: fa\n" in markdown
    assert "\ndir: rtl\n" in markdown
    assert "\n## " in markdown
    assert "> " in markdown and "  \n" in markdown
    assert "\n## یادداشت‌ها" in markdown
    assert "[↩]" in markdown
    assert "<a id=" in markdown

    plain = result.paths["txt"].read_text(encoding="utf-8")
    assert "\n# " in plain or plain.startswith("# ")
    assert "یادداشت‌ها" in plain


def test_isolate_latin_unit_cases() -> None:
    latin = isolate_latin("He said hello.")
    assert latin.count('class="ltr"') == 1
    assert 'dir="ltr"' in latin
    assert "hello.</span>" in latin

    persian = isolate_latin("او گفت: «سلام».")
    assert 'class="ltr"' not in persian

    mixed = isolate_latin('He said "سلام" and left.')
    assert mixed.count('class="ltr"') == 2
    assert "سلام" in mixed
    assert 'He said "' in mixed
    assert 'left.</span>' in mixed

    existing = isolate_latin(
        '<span class="ltr" dir="ltr" lang="en">Already English</span>'
    )
    assert existing.count('class="ltr"') == 1


def test_bidi_isolation_in_exported_chapter(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    database = Database(base / "book.db")
    database.connect()
    try:
        database.add_translation(
            segment_id="c001.s0004",
            pass_kind="human",
            fa_text='He said "سلام" and left.',
            fa_markup='He said "سلام" and left.',
            model="human",
        )
    finally:
        database.close()

    result = _export(isolated_home)
    with zipfile.ZipFile(result.paths["epub"]) as archive:
        body = archive.read("OEBPS/text/chapter-001.xhtml").decode("utf-8")
    assert 'class="ltr"' in body
    assert 'dir="ltr"' in body
    assert "He said" in body
    assert "سلام" in body


def test_incomplete_gate_and_warning_page(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    with pytest.raises(RuntimeFailure) as raised:
        run_export(
            root=isolated_home,
            slug="test-book",
            settings=load_settings(root=isolated_home),
            options=ExportOptions(formats=["epub"]),
        )
    assert "--allow-incomplete" in str(raised.value)
    assert "tj translate" in str(raised.value)

    allowed = run_export(
        root=isolated_home,
        slug="test-book",
        settings=load_settings(root=isolated_home),
        options=ExportOptions(formats=["epub"], allow_incomplete=True),
    )
    assert allowed.incomplete is True
    with zipfile.ZipFile(allowed.paths["epub"]) as archive:
        pages = b"\n".join(
            archive.read(name) for name in archive.namelist() if name.endswith(".xhtml")
        )
    assert b"TRANSLATION INCOMPLETE" in pages
    assert b"--allow-incomplete" in pages


def test_cli_export_smoke(isolated_home: Path, fixtures: dict[str, Path]) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    result = runner.invoke(app, ["export", "--formats", "epub,md,txt"])
    assert result.exit_code == 0, result.stdout
    for extension in ("epub", "md", "txt"):
        assert (base / "out" / f"test-book.fa.{extension}").is_file()

def _unsafe_segment(nodes, markup: str = "⟨1⟩x⟨/1⟩"):
    from tarjoman.export.models import ExportSegment

    return ExportSegment(
        id="c001.s9999",
        idx=1,
        kind="prose",
        source_text="source",
        source_html="<p></p>",
        nodes=nodes,
        fa_text="x",
        fa_markup=markup,
        notes={},
        issues=[],
    )


def test_render_segment_keeps_block_wrapper() -> None:
    from dataclasses import replace

    from tarjoman.export.assets import SourceAssets
    from tarjoman.export.xhtml import render_segment

    prose = _unsafe_segment(
        [{"id": 1, "path": [], "kind": "text", "text": "سلام"}], markup="سلام"
    )
    assert render_segment(prose, SourceAssets()) == "<p>سلام</p>"

    heading = replace(prose, kind="heading", source_html="<h2>عنوان</h2>")
    assert render_segment(heading, SourceAssets()) == "<h2>سلام</h2>"

    quote = replace(prose, kind="quote")
    assert render_segment(quote, SourceAssets()) == "<blockquote>سلام</blockquote>"


def test_active_source_markup_is_sanitized() -> None:
    from tarjoman.export.assets import ImageAsset, SourceAssets
    from tarjoman.export.xhtml import render_segment

    linked = _unsafe_segment(
        [{"id": 1, "path": ["a|href=javascript:alert(1)"], "kind": "text", "text": "click"}]
    )
    rendered = render_segment(linked, SourceAssets())
    assert "javascript:" not in rendered
    assert "<a" not in rendered or "href" not in rendered

    asset = ImageAsset(name="img-x.png", media_type="image/png", data=b"png")
    assets = SourceAssets(
        cover=None,
        images=[asset],
        by_source={"x.png": asset},
    )
    image = _unsafe_segment(
        [
            {
                "id": 1,
                "path": [],
                "kind": "atomic",
                "atomic_html": '<img src="x.png" onerror="alert(1)" alt="a"/>',
            }
        ]
    )
    rendered = render_segment(image, assets)
    assert "onerror" not in rendered
    assert "../images/img-x.png" in rendered


def test_zip_image_budget_rejects_expansion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tarjoman.export import assets

    archive_path = tmp_path / "bomb.epub"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("word/media/big.png", b"x" * 100)
    monkeypatch.setattr(assets, "MAX_ENTRY_BYTES", 10)
    with zipfile.ZipFile(archive_path) as archive, pytest.raises(RuntimeFailure) as raised:
        assets._zip_images(archive, "word/media/", {"total": 0})
    assert "too large" in str(raised.value)


def test_markdown_links_are_scheme_checked_and_escaped() -> None:
    from tarjoman.export.markdown_writer import _inline_markdown

    assert "javascript:" not in _inline_markdown(
        '<a href=" javascript:alert(1)">click</a>', set()
    )
    rendered = _inline_markdown(
        '<a href="x) [pwn](javascript:alert(1)">click</a>', set()
    )
    assert "javascript:" not in rendered
    encoded = _inline_markdown('<a href="x) [pwn]">click</a>', set())
    assert "%29" in encoded
    assert "%20" in encoded


def test_persian_title_is_used_when_set(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    database = Database(base / "book.db")
    database.connect()
    try:
        database.execute("UPDATE books SET title_fa = ?", ("کتاب آزمون",))
    finally:
        database.close()

    result = _export(isolated_home)
    with zipfile.ZipFile(result.paths["epub"]) as archive:
        opf = etree.fromstring(archive.read("OEBPS/content.opf"))
    title = opf.find(".//{http://purl.org/dc/elements/1.1/}title")
    assert title is not None and title.text == "کتاب آزمون"


def test_failed_validation_keeps_existing_epub(
    isolated_home: Path, fixtures: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tarjoman.export.assets import load_source_assets
    from tarjoman.export.epub_writer import write_epub
    from tarjoman.export.loader import load_export_book

    base = make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    settings = load_settings(root=isolated_home)
    book = load_export_book(root=isolated_home, slug="test-book", settings=settings)
    assets = load_source_assets(book)
    target = base / "out" / "existing.epub"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"previous good epub")

    monkeypatch.setattr("tarjoman.export.epub_writer.validate_epub", lambda _path: ["boom"])
    with pytest.raises(RuntimeFailure):
        write_epub(book, target, settings, assets)
    assert target.read_bytes() == b"previous good epub"


def test_broken_epubcheck_does_not_abort_export(
    isolated_home: Path, fixtures: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from tarjoman.export.epub_writer import _run_epubcheck

    make_workspace(isolated_home, fixtures)
    monkeypatch.setattr("tarjoman.export.epub_writer.shutil.which", lambda _name: "epubcheck")

    def fail(*args, **kwargs):
        raise OSError("java missing")

    monkeypatch.setattr("tarjoman.export.epub_writer.subprocess.run", fail)
    _run_epubcheck(Path("missing.epub"), load_settings(root=isolated_home))
