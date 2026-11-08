"""Offline providers and workspace helpers for the M4-M6 tests."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from shahrzad.core.config import load_settings
from shahrzad.core.ids import sha256_text
from shahrzad.core.tokens import estimate_tokens_en
from shahrzad.llm.provider import LLMResult, Message, messages_json
from shahrzad.pipeline.init_pass import init_book

_SENTINEL = re.compile(r"⟨/?\d+⟩|⟦/?\d+⟧|\[\[#?\d+\]\]|\[\[/\d+\]\]")
_WORD = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?")
_SEGMENT = re.compile(
    r'<segment id="([^"]+)">\s*(.*?)\s*</segment>', re.DOTALL
)

_WORDS = [
    "دریا",
    "پیرمرد",
    "قایق",
    "خانه",
    "روز",
    "شب",
    "باد",
    "آب",
    "ماهی",
    "دست",
    "چشم",
    "راه",
    "سنگ",
    "نور",
    "سایه",
    "دور",
    "نزدیک",
    "آرام",
    "سرد",
    "گرم",
    "بزرگ",
    "کوچک",
    "سبک",
    "سنگین",
    "خوب",
    "تازه",
    "کهنه",
    "زندگی",
    "دوست",
    "شهر",
    "روستا",
    "کوه",
    "جنگل",
    "پرنده",
    "آسمان",
    "زمین",
    "ستاره",
    "خورشید",
    "ابر",
    "باران",
    "برف",
    "صدا",
    "سکوت",
    "کتاب",
    "پنجره",
    "دیوار",
    "باغ",
    "گل",
    "اسب",
    "کودک",
    "زن",
    "مرد",
    "کار",
    "رود",
    "ساحل",
    "جزیره",
    "کشتی",
    "شبکه",
    "تصویر",
    "خاطره",
]


def translate_markup(markup: str) -> str:
    parts: list[str] = []
    position = 0
    for match in _SENTINEL.finditer(markup):
        parts.append(_convert(markup[position : match.start()]))
        parts.append(match.group(0))
        position = match.end()
    parts.append(_convert(markup[position:]))
    return "".join(parts)


def _convert(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        word = match.group(0).lower()
        digest = hashlib.sha256(word.encode("utf-8")).hexdigest()
        return _WORDS[int(digest[:8], 16) % len(_WORDS)]

    return _WORD.sub(replace, text)


def _result(payload: dict[str, Any], messages: list[Message]) -> LLMResult:
    text = json.dumps(payload, ensure_ascii=False)
    return LLMResult(
        text=text,
        parsed=payload,
        tokens_in=estimate_tokens_en(messages_json(messages)),
        tokens_out=estimate_tokens_en(text),
        cost_usd=0.0,
        latency_ms=0,
        finish_reason="stop",
    )


def _segments(text: str) -> list[tuple[str, str]]:
    return [(match.group(1), match.group(2)) for match in _SEGMENT.finditer(text)]


class SmartProvider:
    """Deterministic Persian translations that preserve sentinels and word counts."""

    def __init__(
        self,
        *,
        mode: str = "clean",
        fail_segment: str | None = None,
        audit_findings: list[dict[str, Any]] | None = None,
        readthrough_issues: list[dict[str, Any]] | None = None,
    ) -> None:
        self.mode = mode
        self.fail_segment = fail_segment
        self.audit_findings = audit_findings or []
        self.readthrough_issues = readthrough_issues or []
        self.calls: list[dict[str, Any]] = []
        self._critique_calls = 0

    def complete(
        self,
        *,
        messages: list[Message],
        model: str,
        temperature: float | None,
        max_output_tokens: int,
        response_schema: dict[str, Any] | None = None,
        seed: int | None = None,
        thinking: str | None = None,
    ) -> LLMResult:
        content = messages[-1].content
        self.calls.append(
            {
                "model": model,
                "temperature": temperature,
                "content": content,
                "max_output_tokens": max_output_tokens,
            }
        )
        if "You are a master literary translator" in content:
            return self._translate(content, messages)
        if "accuracy check" in content:
            return self._fidelity(content, messages)
        if "Persian literary editor" in content:
            return _result(
                {"issues": [], "verdict": "clean", "segment_verdicts": {}}, messages
            )
        if "You are revising a Persian literary translation" in content:
            return self._revise(content, messages)
        if "Audit one chapter of a Persian translation" in content:
            return _result(
                {
                    "findings": self.audit_findings,
                    "ledger_corrections": [],
                    "verdict": "inconsistent" if self.audit_findings else "consistent",
                },
                messages,
            )
        if "You are a Persian literary critic reading a translated chapter" in content:
            return _result(
                {
                    "issues": self.readthrough_issues[:5],
                    "voice_verdict": "consistent",
                    "chapter_impression": "The chapter reads cleanly in Persian.",
                },
                messages,
            )
        if "Maintain the continuity ledger" in content:
            return _result(
                {
                    "characters": [],
                    "terms": [],
                    "decisions": [],
                    "open_questions": [],
                    "corrections": [],
                },
                messages,
            )
        return _result(
            {
                "summary_en": "x" * 220,
                "continuity": {
                    "characters": [],
                    "relationships": [],
                    "places_and_objects": [],
                    "time": [],
                    "open_threads": [],
                },
                "tone_notes": "",
                "translation_landmines": [],
            },
            messages,
        )

    def _translate(self, content: str, messages: list[Message]) -> LLMResult:
        payload_segments = []
        for segment_id, markup in _segments(content):
            if segment_id == self.fail_segment:
                continue
            payload_segments.append(
                {"id": segment_id, "fa_markup": translate_markup(markup), "notes": ""}
            )
        return _result(
            {"segments": payload_segments, "glossary_suggestions": [], "window_notes": ""},
            messages,
        )

    def _fidelity(self, content: str, messages: list[Message]) -> LLMResult:
        self._critique_calls += 1
        if self.mode in {"omission", "omission_bad"} and self._critique_calls == 1:
            segments = [item for item in _segments(content) if len(item[1].split()) >= 4]
            source = segments[0][1] if segments else ""
            segment_id = segments[0][0] if segments else ""
            quote = " ".join(source.split()[:6])
            return _result(
                {
                    "issues": [
                        {
                            "segment_id": segment_id,
                            "type": "omission",
                            "severity": "major",
                            "quote_source": quote,
                            "quote_target": "",
                            "problem": "the closing clause is missing",
                            "suggested_fix": "restore the clause",
                        }
                    ],
                    "verdict": "major_issues",
                    "segment_verdicts": {segment_id: "major"},
                },
                messages,
            )
        return _result({"issues": [], "verdict": "clean", "segment_verdicts": {}}, messages)

    def _revise(self, content: str, messages: list[Message]) -> LLMResult:
        draft = re.search(r"<draft>\s*(.*?)\s*</draft>", content, re.DOTALL)
        segments = _segments(draft.group(1) if draft else content)
        revised: list[dict[str, str]] = []
        for index, (segment_id, markup) in enumerate(segments):
            if self.mode in {"bad_revision", "omission_bad"}:
                value = _SENTINEL.sub("", markup)
            elif self.mode == "omission" and index == 0:
                value = f"{markup} و بازگشت."
            else:
                value = markup
            revised.append({"id": segment_id, "fa_markup": value, "notes": ""})
        return _result(
            {
                "segments": revised,
                "applied": [
                    {"segment_id": segment_id, "issue_index": 0, "how": "test"}
                    for segment_id, _ in segments
                ],
                "skipped": [],
                "glossary_suggestions": [],
                "window_notes": "",
            },
            messages,
        )


def make_workspace(
    home: Path, fixtures: dict[str, Path], *, slug: str = "test-book", source: str = "epub"
) -> Path:
    init_book(
        root=home,
        source_path=fixtures[source],
        settings=load_settings(root=home),
        slug=slug,
    )
    return home / "books" / slug


def count_versions(db_path: Path) -> dict[str, int]:
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT segment_id, COUNT(*) FROM translations GROUP BY segment_id"
        ).fetchall()
    finally:
        connection.close()
    return {str(segment): int(count) for segment, count in rows}


def issue_codes(db_path: Path) -> dict[str, int]:
    import sqlite3

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(
            "SELECT code, COUNT(*) FROM issues GROUP BY code"
        ).fetchall()
    finally:
        connection.close()
    return {str(code): int(count) for code, count in rows}


def digest(value: str) -> str:
    return sha256_text(value)
