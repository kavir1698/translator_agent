from __future__ import annotations

import json
from pathlib import Path

import pytest

from tarjoman.agents.entity_extractor import scan_proper_nouns
from tarjoman.core.errors import ConfigError
from tarjoman.core.glossary import (
    GlossaryEntry,
    load_glossary_file,
    normalize_term_en,
    write_glossary_yaml,
)
from tarjoman.core.text import compute_style_metrics, split_sentences_en


def test_split_sentences_respects_abbreviations_and_decimals():
    sentences = split_sentences_en("Dr. Smith arrived. He paid 3.14 dollars. Then he left.")
    assert sentences == [
        "Dr. Smith arrived.",
        "He paid 3.14 dollars.",
        "Then he left.",
    ]
    assert split_sentences_en("Mr. Bennet went home.") == ["Mr. Bennet went home."]


def test_style_metrics_are_stable_and_complete():
    paragraphs = [
        "The old man walked home. The water was grey.",
        "«No,» he said. «Not today.»",
    ]
    first = compute_style_metrics(paragraphs)
    second = compute_style_metrics(paragraphs)
    assert first == second
    assert first["words"] > 0
    assert first["sentences"] == 4
    assert first["dialogue_ratio"] > 0
    assert set(first["sentence_length_distribution"]) == {
        "1-9",
        "10-19",
        "20-29",
        "30-39",
        "40-49",
        "50+",
    }
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_proper_noun_scan_skips_sentence_starters():
    segments = [
        {
            "id": "c001.s0001",
            "source_text": "The old man met Santiago near the harbour. He liked Santiago.",
        },
        {
            "id": "c001.s0002",
            "source_text": "Manolin waited. The boy was patient with Manolin.",
        },
    ]
    entities = scan_proper_nouns(segments)
    names = {entity.en for entity in entities}
    assert "Santiago" in names
    assert "Manolin" in names
    assert "The" not in names
    assert "He" not in names


def test_term_normalization_strips_titles_and_possessives():
    assert normalize_term_en("Mr. Bennet's") == "bennet"
    assert normalize_term_en("  SANTIAGO  ") == "santiago"
    assert normalize_term_en("the old man") == "the old man"


def test_glossary_yaml_roundtrip(tmp_path: Path):
    path = tmp_path / "glossary.yaml"
    write_glossary_yaml(
        path,
        book_slug="book",
        entries=[
            GlossaryEntry(
                term_en="Santiago",
                term_fa="سانتیاگو",
                kind="character",
                status="approved",
                confidence=1.0,
            )
        ],
    )
    entries = load_glossary_file(path)
    assert entries[0].term_en == "Santiago"
    assert entries[0].term_fa == "سانتیاگو"
    assert entries[0].kind == "character"


def test_glossary_csv_validation(tmp_path: Path):
    path = tmp_path / "terms.csv"
    path.write_text("en,fa,kind\nSantiago,سانتیاگو,character\nmarlin,,term\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_glossary_file(path)
