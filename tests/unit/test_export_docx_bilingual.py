from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest
from docx import Document
from lxml import etree
from typer.testing import CliRunner

from tarjoman.cli.main import app
from tarjoman.core.config import load_settings
from tarjoman.core.cost import load_model_profile
from tarjoman.core.db import Database
from tarjoman.core.errors import RuntimeFailure
from tarjoman.export.bilingual_html import write_bilingual_html
from tarjoman.export.loader import load_export_book
from tarjoman.pipeline.export_pass import ExportOptions, run_export
from tarjoman.pipeline.translate_pass import TranslateOptions, run_translate

from .support import SmartProvider, make_workspace

runner = CliRunner()
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": _W}


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
    options = ExportOptions(formats=["docx"])
    for key, value in overrides.items():
        setattr(options, key, value)
    return run_export(
        root=home,
        slug="test-book",
        settings=load_settings(root=home),
        options=options,
    )


def _segment_text(base: Path, segment_id: str) -> str:
    database = Database(base / "book.db")
    database.connect()
    try:
        row = database.current_translation(segment_id)
        assert row is not None
        return str(row["fa_text"])
    finally:
        database.close()


def _add_issue(base: Path, segment_id: str) -> None:
    database = Database(base / "book.db")
    database.connect()
    try:
        database.upsert_issue(
            stage="qa",
            code="L14",
            dedupe_key=f"seg:{segment_id}",
            severity="warn",
            message="test issue for the bilingual badge",
            segment_id=segment_id,
        )
    finally:
        database.close()


def _xml(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name)


def _paragraphs(root: etree._Element) -> list[etree._Element]:
    return list(root.xpath("//w:body//w:p", namespaces=_NS))


def _runs(root: etree._Element) -> list[etree._Element]:
    return list(root.xpath("//w:body//w:r", namespaces=_NS))


def test_docx_is_rtl_with_endnotes(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    title = _segment_text(base, "c001.s0001")
    footnote = _segment_text(base, "c001.s0008")

    result = _export(isolated_home)
    assert set(result.paths) == {"docx"}
    path = result.paths["docx"]
    assert path.name == "test-book.fa.docx"
    assert path.is_file()

    document_xml = _xml(path, "word/document.xml")
    styles_xml = _xml(path, "word/styles.xml")
    settings_xml = _xml(path, "word/settings.xml")
    text = document_xml.decode("utf-8")
    styles = styles_xml.decode("utf-8")
    settings = settings_xml.decode("utf-8")

    assert "w:bidi" in text
    assert "w:rtl" in text
    assert 'w:cs="Vazirmatn"' in text
    assert "w:szCs" in text
    assert 'w:bidi="fa"' in styles
    assert "w:mirrorMargins" in settings
    assert "یادداشت‌ها" in text
    assert title in text
    assert footnote in text

    root = etree.fromstring(document_xml)
    for paragraph in _paragraphs(root):
        assert paragraph.find("w:pPr/w:bidi", _NS) is not None
    runs = _runs(root)
    assert runs
    for run in runs:
        assert run.find("w:rPr/w:rtl", _NS) is not None
        fonts = run.find("w:rPr/w:rFonts", _NS)
        assert fonts is not None
        assert fonts.get(f"{{{_W}}}cs") == "Vazirmatn"
        size = run.find("w:rPr/w:sz", _NS)
        size_cs = run.find("w:rPr/w:szCs", _NS)
        assert size is not None and size_cs is not None
        assert size.get(f"{{{_W}}}val") == size_cs.get(f"{{{_W}}}val")

    page = root.find(".//w:pgSz", _NS)
    assert page is not None
    assert page.get(f"{{{_W}}}w") == "8391"
    assert page.get(f"{{{_W}}}h") == "11906"

    style_values = {
        element.get(f"{{{_W}}}val")
        for element in root.xpath("//w:pStyle", namespaces=_NS)
    }
    assert "Heading1" in style_values
    assert "Heading2" in style_values

    document = Document(str(path))
    assert document.core_properties.author == "A. Writer"
    assert document.core_properties.last_modified_by == "Tarjoman"
    assert len(document.tables) == 1
    paragraph_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "یادداشت‌ها" in paragraph_text


def test_bilingual_html_is_optional_and_self_contained(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    source_text = "The old man walked down to the harbour"
    translated = _segment_text(base, "c001.s0003")

    default = run_export(
        root=isolated_home,
        slug="test-book",
        settings=load_settings(root=isolated_home),
        options=ExportOptions(formats=["epub"]),
    )
    assert "bilingual" not in default.paths
    assert not (base / "out" / "test-book.bilingual.html").exists()

    _add_issue(base, "c001.s0003")
    result = run_export(
        root=isolated_home,
        slug="test-book",
        settings=load_settings(root=isolated_home),
        options=ExportOptions(formats=["epub"], bilingual=True),
    )
    path = result.paths["bilingual"]
    assert path.name == "test-book.bilingual.html"
    assert path.is_file()
    html = path.read_text(encoding="utf-8")

    assert 'dir="ltr"' in html
    assert 'dir="rtl"' in html
    assert source_text in html
    assert translated in html
    assert "L14" in html
    assert "warn" in html
    assert "http://" not in html
    assert "https://" not in html
    assert "<script src" not in html.lower()

    settings = load_settings(root=isolated_home)
    settings.export.bilingual_html.include_source = False
    book = load_export_book(root=isolated_home, slug="test-book", settings=settings)
    without_source = write_bilingual_html(book, base / "out" / "no-source.html", settings)
    text = without_source.read_text(encoding="utf-8")
    assert '<td class="src"' not in text
    assert "متن اصلی" not in text
    assert source_text not in text
    assert translated in text


def test_pdf_without_libreoffice_keeps_docx(
    isolated_home: Path,
    fixtures: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    monkeypatch.setattr("tarjoman.export.pdf_convert.shutil.which", lambda _name: None)

    result = _export(isolated_home, pdf=True)
    assert "docx" in result.paths
    assert result.paths["docx"].is_file()
    assert "pdf" not in result.paths
    warning = " ".join(result.warnings)
    assert "soffice --headless --convert-to pdf" in warning
    assert "not print-ready" in warning
    assert "soffice" in warning


def test_export_gate_applies_to_docx(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    with pytest.raises(RuntimeFailure) as raised:
        _export(isolated_home)
    assert "--allow-incomplete" in str(raised.value)

    allowed = _export(isolated_home, allow_incomplete=True)
    assert allowed.incomplete is True
    assert allowed.paths["docx"].is_file()


def test_docx_is_byte_deterministic(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    first = _export(isolated_home)
    before = first.paths["docx"].read_bytes()
    second = _export(isolated_home)
    after = second.paths["docx"].read_bytes()
    assert before == after
    for name in ("word/document.xml", "word/styles.xml", "word/settings.xml"):
        assert _xml(first.paths["docx"], name) == _xml(second.paths["docx"], name), name


def test_cli_docx_and_bilingual(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    result = runner.invoke(app, ["export", "--formats", "docx", "--bilingual"])
    assert result.exit_code == 0, result.stdout
    assert (base / "out" / "test-book.fa.docx").is_file()
    assert (base / "out" / "test-book.bilingual.html").is_file()


def test_cli_pdf_fallback_message(
    isolated_home: Path,
    fixtures: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = make_workspace(isolated_home, fixtures)
    _translate_all(isolated_home)
    monkeypatch.setattr("tarjoman.export.pdf_convert.shutil.which", lambda _name: None)

    result = runner.invoke(app, ["export", "--formats", "epub", "--pdf"])
    assert result.exit_code == 0, result.stdout
    assert (base / "out" / "test-book.fa.epub").is_file()
    assert (base / "out" / "test-book.fa.docx").is_file()
    output = " ".join(result.stdout.split())
    assert "soffice --headless --convert-to pdf" in output
    assert "not print-ready" in output

def test_pdf_conversion_verifies_non_empty_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tarjoman.export.pdf_convert import convert_docx_to_pdf

    docx = tmp_path / "book.fa.docx"
    docx.write_bytes(b"fake docx")

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        assert kwargs["check"] is False
        out_dir = Path(command[command.index("--outdir") + 1])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{docx.stem}.pdf").write_bytes(b"%PDF-1.4\nfake")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("tarjoman.export.pdf_convert.shutil.which", lambda _name: "soffice")
    monkeypatch.setattr("tarjoman.export.pdf_convert.subprocess.run", fake_run)

    result = convert_docx_to_pdf(docx, tmp_path)

    assert result.available is True
    assert result.path is not None and result.path.is_file()
    assert result.path.read_bytes().startswith(b"%PDF")
    assert "not print-ready" in result.message

def test_pdf_does_not_reuse_stale_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tarjoman.export.pdf_convert import convert_docx_to_pdf

    docx = tmp_path / "book.fa.docx"
    docx.write_bytes(b"fake docx")
    stale = tmp_path / "book.fa.pdf"
    stale.write_bytes(b"%PDF-1.4\nold")

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN003
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("tarjoman.export.pdf_convert.shutil.which", lambda _name: "soffice")
    monkeypatch.setattr("tarjoman.export.pdf_convert.subprocess.run", fake_run)

    result = convert_docx_to_pdf(docx, tmp_path)

    assert result.available is True
    assert result.path is None
    assert "did not create" in result.message
