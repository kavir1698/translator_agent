from __future__ import annotations

import json
from pathlib import Path

from shahrzad.agents.translator import Translator
from shahrzad.context.glossary_match import match_terms
from shahrzad.context.packer import PackState, build_pack, segment_markup
from shahrzad.core.cost import load_model_profile
from shahrzad.core.db import Database
from shahrzad.llm.provider import LLMResult
from shahrzad.persian.lint import GlossaryTerm
from shahrzad.pipeline.window import plan_hash, plan_windows

from .support import SmartProvider, make_workspace


def _segment(idx: int, *, kind: str = "prose", tokens: int = 100, status: str = "pending",
             dialogue: float = 0.0, text: str | None = None,
             nodes: list[dict] | None = None) -> dict:
    return {
        "id": f"c001.s{idx:04d}",
        "chapter_id": 1,
        "idx": idx,
        "kind": kind,
        "status": status,
        "tokens_est": tokens,
        "dialogue_ratio": dialogue,
        "source_text": text or f"Paragraph {idx} with several words in it.",
        "source_html": f"<p>{text or f'Paragraph {idx}'}</p>",
        "nodes_json": json.dumps(nodes or []),
    }


def test_plan_windows_deterministic_and_bounded() -> None:
    segments = [_segment(index, tokens=200) for index in range(1, 11)]
    windows = plan_windows(
        segments, chapter_id=1, chapter_idx=1, max_paragraphs=4, max_source_tokens=500
    )
    assert plan_hash(windows) == plan_hash(
        plan_windows(
            segments, chapter_id=1, chapter_idx=1, max_paragraphs=4, max_source_tokens=500
        )
    )
    assert all(len(window.segments) <= 4 for window in windows)
    assert all(window.tokens <= 500 for window in windows)
    assert [window.id for window in windows] == [f"c001.w{index:03d}" for index in range(1, 6)]
    assert windows[1].overlap == windows[0].segments[-2:]


def test_heading_never_alone_and_oversized_segment_single() -> None:
    segments = [
        _segment(1, kind="heading", tokens=10),
        _segment(2, tokens=100),
        _segment(3, tokens=2000),
        _segment(4, tokens=100),
    ]
    windows = plan_windows(segments, chapter_id=1, chapter_idx=1, max_paragraphs=2)
    assert len(windows[0].segments) >= 2
    assert any(len(window.segments) == 1 and window.segments[0]["tokens_est"] == 2000 for window in windows)


def test_table_markup_keeps_cell_separators() -> None:
    nodes = [
        {"id": 1, "path": ["tr~1", "th~2"], "kind": "text", "text": "Name"},
        {"id": 2, "path": ["tr~1", "th~3"], "kind": "text", "text": "Value"},
        {"id": 3, "path": ["tr~4", "td~5"], "kind": "text", "text": "Alpha"},
        {"id": 4, "path": ["tr~4", "td~6"], "kind": "text", "text": "One"},
    ]
    segment = _segment(1, kind="table", nodes=nodes)
    markup = segment_markup(segment)
    assert " | " in markup
    assert "\n" in markup
    assert markup.count("⟨") == 8


def test_pack_omits_empty_blocks_and_matches_glossary() -> None:
    terms = [
        GlossaryTerm(en="Santiago", fa="سانتیاگو", kind="character"),
        GlossaryTerm(en="marlin", fa="مارلین", kind="term"),
    ]
    matched = match_terms("Santiago fought the marlin", terms)
    assert [term.en for term in matched] == ["Santiago", "marlin"]
    segment = _segment(1, text="Santiago fought the marlin.")
    variables = build_pack(
        window_segments=[segment],
        todo_segments=[segment],
        state=PackState(glossary_terms=tuple(terms)),
    )
    assert "Santiago = سانتیاگو" in variables["glossary_block"]
    assert variables["book_rules"] == ""
    assert variables["anchors"] == ""
    assert variables["cast_block"] == ""
    assert variables["chapter_summary"] == ""
    # The variable suffix changes but the stable blocks stay byte-identical.
    other = _segment(2, text="Another paragraph.")
    second = build_pack(
        window_segments=[other],
        todo_segments=[other],
        state=PackState(glossary_terms=tuple(terms)),
    )
    for key in ("glossary_block", "book_rules", "anchors", "brief_card", "cast_block"):
        assert variables[key] == second[key]


class _BrokenThenValid(SmartProvider):
    def __init__(self, segment_id: str) -> None:
        super().__init__()
        self.segment_id = segment_id
        self.translation_calls = 0

    def complete(self, **kwargs):
        content = kwargs["messages"][-1].content
        if "You are a master literary translator" in content:
            self.translation_calls += 1
            if self.translation_calls == 1:
                return LLMResult(
                    text=json.dumps(
                        {
                            "segments": [
                                {
                                    "id": self.segment_id,
                                    "fa_markup": "پیرمرد رفت",
                                    "notes": "",
                                }
                            ],
                            "glossary_suggestions": [],
                            "window_notes": "",
                        },
                        ensure_ascii=False,
                    ),
                    parsed={
                        "segments": [
                            {"id": self.segment_id, "fa_markup": "پیرمرد رفت", "notes": ""}
                        ],
                        "glossary_suggestions": [],
                        "window_notes": "",
                    },
                    tokens_in=10,
                    tokens_out=10,
                    finish_reason="stop",
                )
        return super().complete(**kwargs)


def test_translator_retries_broken_markup(
    isolated_home: Path, fixtures: dict[str, Path]
) -> None:
    base = make_workspace(isolated_home, fixtures)
    db = Database(base / "book.db")
    db.initialize()
    segment = db.fetchone(
        "SELECT * FROM segments WHERE kind = 'prose' AND nodes_json != '[]' LIMIT 1"
    )
    assert segment is not None
    translator = Translator()
    provider = _BrokenThenValid(str(segment["id"]))
    from shahrzad.context.packer import PackState, build_pack

    variables = build_pack(
        window_segments=[segment], todo_segments=[segment], state=PackState()
    )
    outcome = translator.translate(
        provider=provider,
        tier=load_model_profile(isolated_home / "configs/models/deepseek.yaml").tier("balanced"),
        db=db,
        run_id=None,
        cache=None,
        variables=variables,
        segments=[segment],
    )
    db.close()
    assert provider.translation_calls == 2
    assert outcome.strict_retry
    assert outcome.markup_problems == {}
