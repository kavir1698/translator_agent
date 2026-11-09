from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from shahrzad.core.errors import ConfigError, SchemaValidationError
from shahrzad.core.models import (
    BookBrief,
    Critique,
    JudgeVerdict,
    TranslatorOutput,
    normalize_severity,
)
from shahrzad.llm.promptlib import load, load_all
from shahrzad.llm.schema import (
    build_response_schema,
    canonical_quote,
    check_ids,
    extract_json_object,
    parse_model,
    schema_hash,
)


def test_all_prompts_load(repo_root: Path):
    prompts = load_all(repo_root / "src" / "shahrzad" / "prompts")
    assert len(prompts) == 13
    assert prompts["p05_translator"].version == 4
    assert prompts["p05_translator"].schema_name == "TranslatorOutput"
    assert prompts["p12_judge"].temperature == 0.2


def test_render_is_strict(repo_root: Path):
    prompt = load("p01_brief", repo_root / "src" / "shahrzad" / "prompts")
    rendered = prompt.render(
        {
            "book_title": "T",
            "book_author": "A",
            "sample_text": "S",
            "style_metrics_json": "{}",
            "user_notes": "",
        }
    )
    assert "TITLE: T" in rendered
    with pytest.raises(ConfigError):
        prompt.render({"book_title": "T"})


def test_hash_is_stable(repo_root: Path):
    prompt = load("p01_brief", repo_root / "src" / "shahrzad" / "prompts")
    variables = {
        "book_title": "T",
        "book_author": "A",
        "sample_text": "S",
        "style_metrics_json": "{}",
        "user_notes": "",
    }
    assert prompt.hash(prompt.render(variables)) == prompt.hash(prompt.render(variables))


def test_schema_inlines_defs():
    schema = build_response_schema(TranslatorOutput)
    assert "$defs" not in json.dumps(schema)
    assert schema["properties"]["segments"]["type"] == "array"
    assert "segments" in schema["required"]


def test_extract_json_from_prose():
    payload = extract_json_object('Here you go:\n{"a": 1} done')
    assert payload == {"a": 1}
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(SchemaValidationError):
        extract_json_object("no json here")


def test_parse_model_and_extra_forbidden():
    text = '{"segments": [{"id": "c001.s0001", "fa_markup": "سلام"}], "window_notes": ""}'
    output = parse_model(TranslatorOutput, text)
    assert output.segments[0].id == "c001.s0001"
    with pytest.raises(SchemaValidationError):
        parse_model(TranslatorOutput, '{"segments": [], "unknown": true}')


def test_critique_carries_agent_severity():
    critique = parse_model(
        Critique,
        json.dumps(
            {
                "issues": [
                    {
                        "segment_id": "c001.s0001",
                        "type": "omission",
                        "severity": "major",
                        "problem": "clause missing",
                    }
                ],
                "verdict": "major_issues",
            }
        ),
    )
    assert critique.issues[0].severity == "major"
    assert normalize_severity("major").value == "error"
    with pytest.raises(ValueError):
        normalize_severity("error")


def test_judge_scores_validated():
    good = {
        "scores": {
            "fidelity": 4,
            "naturalness": 4,
            "style_fidelity": 3,
            "terminology": 5,
            "cultural_adaptation": 4,
        },
        "verdict": "pass",
        "rationale": "ok",
    }
    assert JudgeVerdict.model_validate(good).scores["fidelity"] == 4
    with pytest.raises(ValidationError):
        JudgeVerdict.model_validate({**good, "scores": {"fidelity": 4}})


def test_quote_canonicalization_and_ids():
    assert canonical_quote("  ⟨2⟩سلام\nدنیا⟨/2⟩  ") == "سلام دنیا"
    check_ids(["a", "b"], ["b", "a"])
    with pytest.raises(SchemaValidationError):
        check_ids(["a", "b"], ["a"])
    assert schema_hash(TranslatorOutput) == schema_hash(build_response_schema(TranslatorOutput))


def test_book_brief_rule_counts():
    base = {
        "genre": "g",
        "subgenre": "s",
        "era_and_setting": "e",
        "pov": "p",
        "narrative_tense": "t",
        "narrative_distance": "d",
        "register": "r",
        "register_target_fa": "literary-standard",
        "address_policy": "a",
        "sentence_rhythm": "s",
        "dialogue_style": "d",
        "figurative_language": "f",
        "humor_irony": "h",
        "cultural_references_policy": "c",
        "name_policy": "n",
        "measurement_policy": "m",
        "foreign_text_policy": "f",
        "taboo_policy": "t",
        "footnote_policy": "sparing",
        "do_rules": ["a", "b", "c", "d", "e"],
        "dont_rules": ["a", "b", "c", "d", "e"],
    }
    assert BookBrief.model_validate(base).footnote_policy == "sparing"
    with pytest.raises(ValidationError):
        BookBrief.model_validate({**base, "do_rules": ["only one"]})
