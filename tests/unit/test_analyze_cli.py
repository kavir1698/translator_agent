from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from shahrzad.cli.main import app
from shahrzad.core.config import load_settings
from shahrzad.llm.mock_provider import MockProvider
from shahrzad.pipeline.init_pass import init_book

runner = CliRunner()


def _brief_payload() -> dict:
    return {
        "genre": "literary fiction",
        "subgenre": "novella",
        "era_and_setting": "a harbour village",
        "pov": "third person limited",
        "narrative_tense": "past simple",
        "narrative_distance": "close",
        "register": "plain and concrete",
        "register_target_fa": "literary-standard",
        "address_policy": "شما for strangers, تو for intimates",
        "sentence_rhythm": "short declaratives",
        "dialogue_style": "quoted turns in «...»",
        "figurative_language": "sea imagery, never explained",
        "humor_irony": "dry understatement",
        "cultural_references_policy": "keep references",
        "name_policy": "consistent transliteration",
        "measurement_policy": "keep source units",
        "foreign_text_policy": "keep foreign phrases",
        "taboo_policy": "faithful",
        "footnote_policy": "sparing",
        "style_references": [],
        "do_rules": ["one", "two", "three", "four", "five"],
        "dont_rules": ["one", "two", "three", "four", "five"],
        "key_motifs": [],
        "uncertainties": [],
    }


def _init(isolated_home: Path, fixtures: dict[str, Path]) -> Path:
    init_book(
        root=isolated_home,
        source_path=fixtures["epub"],
        settings=load_settings(root=isolated_home),
        slug="test-book",
    )
    return isolated_home / "books" / "test-book"


def test_review_brief_exits_three_then_approve(
    isolated_home: Path, fixtures: dict[str, Path], monkeypatch
):
    base = _init(isolated_home, fixtures)
    provider = MockProvider(script=[_brief_payload()])
    monkeypatch.setattr(
        "shahrzad.cli.analyze_cmd.build_provider", lambda _context: provider
    )
    result = runner.invoke(app, ["analyze", "--review-brief"])
    assert result.exit_code == 3, result.stdout
    assert (base / "artifacts" / "brief.draft.yaml").is_file()
    assert not (base / "artifacts" / "brief.yaml").exists()
    assert "waiting for approval" in result.stdout.lower()

    monkeypatch.setattr(
        "shahrzad.cli.analyze_cmd.build_provider",
        lambda _context: MockProvider(script=[]),
    )
    approved = runner.invoke(app, ["analyze", "--approve"])
    assert approved.exit_code == 0, approved.stdout
    assert (base / "artifacts" / "brief.yaml").is_file()
