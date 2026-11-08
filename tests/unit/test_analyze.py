from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from shahrzad.core.config import load_settings, model_profile
from shahrzad.core.db import Database
from shahrzad.llm.mock_provider import MockProvider
from shahrzad.persian.lint import (
    LintSegment,
    LintTranslation,
    LintWindow,
    check_window,
)
from shahrzad.pipeline.analyze_pass import build_sample, run_analyze
from shahrzad.pipeline.glossary_ops import approved_terms
from shahrzad.pipeline.init_pass import init_book
from tests.fixtures.build import ENTITY_NAMES


def _brief_payload() -> dict[str, Any]:
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
        "sentence_rhythm": "short declaratives and deliberate repetition",
        "dialogue_style": "short quoted turns in «...»",
        "figurative_language": "sea and weather imagery, never explained",
        "humor_irony": "dry understatement",
        "cultural_references_policy": "keep references, explain nothing",
        "name_policy": "consistent Persian transliteration",
        "measurement_policy": "keep source units",
        "foreign_text_policy": "keep foreign phrases, no footnote",
        "taboo_policy": "faithful",
        "footnote_policy": "sparing",
        "style_references": ["a Persian translator with a spare, concrete voice"],
        "do_rules": [
            "keep sentences short",
            "preserve deliberate repetition",
            "translate images, do not explain them",
            "keep dialogue in «...»",
            "keep the source's ambiguities",
        ],
        "dont_rules": [
            "do not use می‌باشد",
            "do not add decorative adjectives",
            "do not explain idioms in the prose",
            "do not flatten the voice",
            "do not leave English words without cause",
        ],
        "key_motifs": [{"motif": "the sea", "why_it_matters": "returns in every chapter"}],
        "uncertainties": [],
    }


def _summary_payload() -> dict[str, Any]:
    return {
        "summary_en": (
            "The old man walks to the harbour before dawn and watches the boats. "
            "The villagers prepare for a change in the weather while the gulls follow him. "
            "By evening he sits alone in the kitchen and remembers his brother."
        ),
        "continuity": {
            "characters": ["the old man"],
            "relationships": [],
            "places_and_objects": ["harbour", "kitchen"],
            "time": ["one day"],
            "open_threads": ["the letter that never came"],
        },
        "tone_notes": "spare, patient, concrete",
        "translation_landmines": ["the ambiguous final sentence"],
    }


def _named_entities_payload(names: list[str]) -> dict[str, Any]:
    return {
        "entities": [
            {
                "en": name,
                "kind": "character" if index < 20 else "place",
                "surface_forms": [name],
                "count": 5,
                "gender": "unknown",
                "role": "recurring entity",
                "context_quote": f"{name} appeared again.",
                "first_segment_id": "c001.s0001",
                "needs_translation": True,
                "notes": "",
            }
            for index, name in enumerate(names)
        ],
        "uncertain": [],
    }


def _named_proposals_payload(names: list[str]) -> dict[str, Any]:
    return {
        "proposals": [
            {
                "en": name,
                "kind": "character" if index < 20 else "place",
                "fa": "واژه",
                "alternatives": [],
                "transliteration": "",
                "gender": "unknown",
                "confidence": 0.95,
                "rationale": "one fixed Persian form",
                "aliases_fa": [],
                "notes": "",
            }
            for index, name in enumerate(names)
        ],
        "refuse": [],
    }


def _prepare(
    isolated_home: Path,
    fixtures: dict[str, Path],
    *,
    glossary: Path | None = None,
    overrides: dict[str, Any] | None = None,
    fixture_key: str = "epub",
) -> tuple[Path, Any, Any]:
    base_settings = load_settings(root=isolated_home)
    init_book(
        root=isolated_home,
        source_path=fixtures[fixture_key],
        settings=base_settings,
        slug="test-book",
        glossary_path=str(glossary) if glossary else None,
    )
    settings = load_settings(
        root=isolated_home, slug="test-book", overrides=overrides or {}
    )
    profile = model_profile(settings, isolated_home)
    return isolated_home / "books" / "test-book", settings, profile


def test_analyze_auto_freezes_brief_and_builds_candidates(isolated_home, fixtures):
    base, settings, profile = _prepare(
        isolated_home,
        fixtures,
        fixture_key="names",
        overrides={"pipeline": {"analyze": {"brief_approval": "auto"}}},
    )
    provider = MockProvider(
        script=[
            _brief_payload(),
            _summary_payload(),
            _summary_payload(),
            _named_entities_payload(ENTITY_NAMES),
            _named_proposals_payload(ENTITY_NAMES),
        ]
    )
    result = run_analyze(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        profile=profile,
        provider=provider,
        auto_glossary=True,
    )
    assert result.run_id
    assert result.mode == "auto"
    assert load_settings(root=isolated_home, slug="test-book").glossary.mode == "auto"
    assert result.frozen is True
    assert result.paused_for_brief is False
    assert result.summaries == 2
    assert result.entities >= 20
    assert result.proposals >= 20
    assert (base / "artifacts" / "brief.yaml").is_file()
    assert (base / "artifacts" / "brief.draft.yaml").is_file()
    assert (base / "artifacts" / "glossary.candidates.yaml").is_file()
    assert (base / "artifacts" / "chapter_summaries.jsonl").is_file()

    candidates = yaml.safe_load(
        (base / "artifacts" / "glossary.candidates.yaml").read_text(encoding="utf-8")
    )
    assert len(candidates["candidates"]) >= 20
    counts = [candidate["count"] for candidate in candidates["candidates"]]
    assert counts == sorted(counts, reverse=True)

    with Database(base / "book.db") as db:
        book = db.get_book("test-book")
        assert book is not None
        assert book["brief_hash"]
        assert book["source_metrics_json"] != "{}"
        summaries = db.chapter_summaries(int(book["id"]))
        assert len(summaries) == 2
        approved = db.glossary_entries(int(book["id"]), status="approved")
        assert len(approved) >= 20
        run = db.get_run(result.run_id)
        assert run is not None
        assert run["status"] == "completed"
        assert len(db.calls_for_run(result.run_id)) == 5

    manifest = json.loads(
        (base / "runs" / str(result.run_id) / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "completed"
    assert manifest["stats"]["calls"] == 5
    assert manifest["stats"]["sample"]["tokens"] <= 18000


def test_analyze_off_writes_proper_nouns_and_no_glossary(isolated_home, fixtures):
    base, settings, profile = _prepare(
        isolated_home, fixtures, overrides={"glossary": {"mode": "off"}}
    )
    provider = MockProvider(script=[_brief_payload(), _summary_payload(), _summary_payload()])
    result = run_analyze(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        profile=profile,
        provider=provider,
    )
    assert result.mode == "off"
    assert result.glossary_entries == 0
    assert (base / "artifacts" / "proper_nouns.yaml").is_file()
    assert not (base / "artifacts" / "glossary.candidates.yaml").exists()
    with Database(base / "book.db") as db:
        book = db.get_book("test-book")
        assert book is not None
        assert db.glossary_entries(int(book["id"])) == []


def test_provided_glossary_import_enables_l03(isolated_home, fixtures, tmp_path):
    terms = tmp_path / "terms.yaml"
    terms.write_text(
        yaml.safe_dump(
            {
                "entries": [
                    {"term_en": "Santiago", "term_fa": "سانتیاگو", "kind": "character"},
                    {"term_en": "marlin", "term_fa": "مارلین", "kind": "term"},
                ]
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    base, settings, profile = _prepare(isolated_home, fixtures, glossary=terms)
    assert settings.glossary.mode == "provided"
    assert settings.glossary.path
    provider = MockProvider(script=[_brief_payload(), _summary_payload(), _summary_payload()])
    result = run_analyze(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        profile=profile,
        provider=provider,
    )
    assert result.mode == "provided"
    assert result.glossary_entries == 2
    assert (base / "artifacts" / "glossary.yaml").is_file()

    with Database(base / "book.db") as db:
        book = db.get_book("test-book")
        assert book is not None
        terms_in_db = approved_terms(db, int(book["id"]))
    assert {term.en for term in terms_in_db} == {"Santiago", "marlin"}

    window = LintWindow(
        id="w001",
        segments=[LintSegment(id="c001.s0001", source_text="Santiago rowed alone.")],
    )
    missing = check_window(
        window,
        {"c001.s0001": LintTranslation(fa_text="پیرمرد تنها پارو زد.")},
        terms_in_db,
    )
    assert any(issue.code == "L03" for issue in missing)
    present = check_window(
        window,
        {"c001.s0001": LintTranslation(fa_text="سانتیاگو تنها پارو زد.")},
        terms_in_db,
    )
    assert not any(issue.code == "L03" for issue in present)


def test_review_brief_pauses_until_approved(isolated_home, fixtures):
    base, settings, profile = _prepare(
        isolated_home, fixtures, overrides={"pipeline": {"analyze": {"brief_approval": "auto"}}}
    )
    provider = MockProvider(script=[_brief_payload()])
    paused = run_analyze(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        profile=profile,
        provider=provider,
        review_brief=True,
    )
    assert paused.paused_for_brief is True
    assert paused.frozen is False
    assert paused.draft_path is not None and paused.draft_path.is_file()
    assert not (base / "artifacts" / "brief.yaml").exists()
    assert len(provider.calls) == 1
    with Database(base / "book.db") as db:
        run = db.get_run(str(paused.run_id))
        assert run is not None and run["status"] == "paused"

    approved = run_analyze(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        profile=profile,
        provider=MockProvider(script=[]),
        approve=True,
    )
    assert approved.frozen is True
    assert (base / "artifacts" / "brief.yaml").is_file()
    with Database(base / "book.db") as db:
        book = db.get_book("test-book")
        assert book is not None and book["brief_hash"]


def test_draft_only_writes_draft_without_freezing(isolated_home, fixtures):
    base, settings, profile = _prepare(
        isolated_home, fixtures, overrides={"pipeline": {"analyze": {"brief_approval": "auto"}}}
    )
    result = run_analyze(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        profile=profile,
        provider=MockProvider(script=[_brief_payload()]),
        draft_only=True,
    )
    assert result.frozen is False
    assert result.paused_for_brief is False
    assert result.draft_path is not None and result.draft_path.is_file()
    assert not (base / "artifacts" / "brief.yaml").exists()
    with Database(base / "book.db") as db:
        run = db.get_run(str(result.run_id))
        assert run is not None and run["status"] == "completed"


def test_only_chapter_limits_summaries(isolated_home, fixtures):
    base, settings, profile = _prepare(isolated_home, fixtures)
    with Database(base / "book.db") as db:
        book = db.get_book("test-book")
        assert book is not None
        from shahrzad.agents.analyst import default_brief, freeze_brief

        _path, digest = freeze_brief(base, book_slug="test-book", brief=default_brief())
        db.set_book_brief_hash(int(book["id"]), digest)

    result = run_analyze(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        profile=profile,
        provider=MockProvider(script=[_summary_payload()]),
        only_chapter=1,
    )
    assert result.summaries == 1
    with Database(base / "book.db") as db:
        book = db.get_book("test-book")
        assert book is not None
        assert len(db.chapter_summaries(int(book["id"]))) == 1


def test_sample_builder_is_deterministic_and_bounded(isolated_home, fixtures):
    base, _settings, _profile = _prepare(isolated_home, fixtures)
    with Database(base / "book.db") as db:
        book = db.get_book("test-book")
        assert book is not None
        first = build_sample(db, int(book["id"]), budget=18000)
        second = build_sample(db, int(book["id"]), budget=18000)
        small = build_sample(db, int(book["id"]), budget=1000)
    assert first.manifest == second.manifest
    assert first.text == second.text
    assert first.parts
    assert first.tokens <= 18000
    assert small.tokens <= 1000
    assert any(part.chapter_idx == 1 for part in first.parts)
