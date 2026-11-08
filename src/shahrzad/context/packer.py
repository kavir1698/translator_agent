"""Budgeted, stable-prefix context assembly for translation prompts (plan 6.4)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from shahrzad.agents.analyst import brief_card
from shahrzad.core.models import BookBrief
from shahrzad.ingest.inline import render_with_sentinels
from shahrzad.persian.lint import GlossaryTerm

from .glossary_match import GLOSSARY_ENTRY_CAP, NameList, anchor_block, glossary_block, match_terms

_CAST_FIELDS = {"kind": "character"}


@dataclass(frozen=True)
class PackState:
    brief: BookBrief | None = None
    glossary_terms: tuple[GlossaryTerm, ...] = ()
    proper_nouns: NameList = field(default_factory=NameList)
    anchors: tuple[dict[str, Any], ...] = ()
    chapter_summary: str = ""
    rolling_summary: str = ""
    ledger: tuple[Any, ...] = ()
    cast: tuple[Any, ...] = ()


def book_rules(brief: BookBrief | None) -> str:
    if brief is None:
        return ""
    lines = [f"register: {brief.register} → {brief.register_target_fa}"]
    lines.append(f"address: {brief.address_policy}")
    if brief.do_rules:
        lines.append("do:")
        lines.extend(f"- {rule}" for rule in brief.do_rules)
    if brief.dont_rules:
        lines.append("dont:")
        lines.extend(f"- {rule}" for rule in brief.dont_rules)
    return "\n".join(lines)


def _nodes(segment: Any) -> list[dict[str, Any]]:
    raw = segment["nodes_json"] if hasattr(segment, "keys") else segment.get("nodes_json", "[]")
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "[]")
        except json.JSONDecodeError:
            data = []
        return data if isinstance(data, list) else []
    return list(raw or [])


def _table_markup(nodes: Sequence[dict[str, Any]]) -> str:
    """Keep row/cell separators visible so the model cannot fuse table cells."""
    rows: list[list[str]] = []
    current_row: list[str] = []
    last_row: str | None = None
    for node in nodes:
        path = [str(step) for step in node.get("path", [])]
        row_key = path[0] if path else ""
        if last_row is not None and row_key != last_row:
            rows.append(current_row)
            current_row = []
        current_row.append(render_with_sentinels([node]))
        last_row = row_key
    if current_row:
        rows.append(current_row)
    return "\n".join(" | ".join(cells) for cells in rows)


def segment_markup(segment: Any) -> str:
    """Sentinel-marked source text for one segment (plain text when it has no markup)."""
    nodes = _nodes(segment)
    if not nodes:
        return str(segment["source_text"])
    if str(segment["kind"]) == "table":
        rendered = _table_markup(nodes)
    else:
        rendered = render_with_sentinels(nodes)
    return rendered if rendered.strip() else str(segment["source_text"])


def render_segments(segments: Sequence[Any]) -> str:
    return "\n\n".join(
        f"<segment id=\"{segment['id']}\">\n{segment_markup(segment)}\n</segment>"
        for segment in segments
    )


def render_draft(segments: Sequence[Any], translations: dict[str, Any]) -> str:
    blocks: list[str] = []
    for segment in segments:
        segment_id = str(segment["id"])
        row = translations.get(segment_id)
        if row is None:
            continue
        blocks.append(
            f"<segment id=\"{segment_id}\">\n{row['fa_markup'] if hasattr(row, 'keys') else row}\n</segment>"
        )
    return "\n\n".join(blocks)


def window_instruction(segments: Sequence[Any]) -> str:
    kinds = {str(segment["kind"]) for segment in segments}
    notes: list[str] = []
    if "verse" in kinds:
        notes.append("This window contains verse; keep the line breaks and stanza structure.")
    if "table" in kinds:
        notes.append("This window contains a table; keep cell boundaries.")
    if "footnote" in kinds:
        notes.append("This window contains footnote text; translate it as a note, not as narrative.")
    if "heading" in kinds and len(segments) == 1:
        notes.append("This window is a heading only; keep it short and unpunctured.")
    return " ".join(notes) or "Translate the segments in order."


def _value(row: Any) -> dict[str, Any]:
    raw = row["value_json"] if hasattr(row, "keys") else row.get("value", "{}")
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return dict(raw or {})


def cast_block(window_source: str, cast_rows: Sequence[Any]) -> str:
    ranked: list[tuple[int, Any, dict[str, Any]]] = []
    for position, row in enumerate(cast_rows):
        value = _value(row)
        en = str(value.get("en", row["key"] if hasattr(row, "keys") else ""))
        present = 1 if en and re.search(rf"(?<!\w){re.escape(en)}(?!\w)", window_source, re.I) else 0
        ranked.append((-present, position, row))
    ranked.sort(key=lambda item: (item[0], item[1]))
    lines: list[str] = []
    for _, _, row in ranked[:8]:
        value = _value(row)
        en = str(value.get("en", row["key"] if hasattr(row, "keys") else ""))
        parts = [en, str(value.get("fa", ""))]
        for key in ("role", "gender"):
            if value.get(key):
                parts.append(str(value[key]))
        address = value.get("address_forms") or {}
        if isinstance(address, dict) and address:
            parts.append(
                "address: "
                + "; ".join(f"{other}={form}" for other, form in sorted(address.items()))
            )
        lines.append("- " + " · ".join(part for part in parts if part))
    return "\n".join(lines)


def ledger_slice(window_source: str, ledger_rows: Sequence[Any], *, limit: int = 15) -> str:
    relevant: list[str] = []
    for row in ledger_rows:
        kind = str(row["kind"])
        key = str(row["key"])
        value = _value(row)
        names = [name for name in (key, str(value.get("en", ""))) if name.strip()]
        if names and not any(
            re.search(rf"(?<!\w){re.escape(name.strip())}(?!\w)", window_source, re.I)
            for name in names
        ):
            continue
        if kind == "decision":
            text = f"{key}: {value if not isinstance(value, dict) else json.dumps(value, ensure_ascii=False)}"
        elif kind == "character":
            fields = [str(value.get("en", key)), str(value.get("fa", ""))]
            if value.get("role"):
                fields.append(str(value["role"]))
            text = " · ".join(field for field in fields if field)
        else:
            text = f"{kind}: {key} = {value.get('fa', value)}"
        relevant.append(text)
    return "\n".join(f"- {item}" for item in relevant[-limit:])


def build_pack(
    *,
    window_segments: Sequence[Any],
    todo_segments: Sequence[Any],
    state: PackState,
    prev_segments: Sequence[Any] = (),
    prev_translations: dict[str, Any] | None = None,
    extra_instruction: str = "",
) -> dict[str, str]:
    """Variables for P05/P06/P07/P08. Empty blocks render as empty strings."""
    source_text = " ".join(str(segment["source_text"]) for segment in todo_segments)
    if len(state.glossary_terms) <= GLOSSARY_ENTRY_CAP:
        glossary = glossary_block(state.glossary_terms)
    else:
        prefix = glossary_block(state.glossary_terms[:GLOSSARY_ENTRY_CAP])
        overflow = match_terms(source_text, state.glossary_terms[GLOSSARY_ENTRY_CAP:])
        glossary = "\n".join(part for part in (prefix, glossary_block(overflow)) if part)
    if not glossary:
        glossary = state.proper_nouns.block()

    prev_source = "\n".join(str(segment["source_text"]) for segment in prev_segments)
    translations = prev_translations or {}
    prev_translation = "\n".join(
        str(
            translations[str(segment["id"])]["fa_text"]
            if hasattr(translations[str(segment["id"])], "keys")
            else translations[str(segment["id"])]
        )
        for segment in prev_segments
        if str(segment["id"]) in translations
    )
    instruction = " ".join(
        part for part in (window_instruction(todo_segments), extra_instruction) if part
    )
    return {
        "book_rules": book_rules(state.brief),
        "brief_card": brief_card(state.brief) if state.brief is not None else "",
        "glossary_block": glossary,
        "cast_block": cast_block(source_text, state.cast),
        "anchors": anchor_block(state.anchors),
        "chapter_summary": state.chapter_summary,
        "rolling_summary": state.rolling_summary,
        "ledger_slice": ledger_slice(source_text, state.ledger),
        "prev_source": prev_source,
        "prev_translation": prev_translation,
        "segments": render_segments(todo_segments),
        "window_instruction": instruction,
    }
