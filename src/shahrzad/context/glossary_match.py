"""Deterministic window-level glossary subsetting and block rendering (plan 6.4, D14)."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from shahrzad.core.errors import ConfigError
from shahrzad.persian.lint import GlossaryTerm

GLOSSARY_ENTRY_CAP = 300
WINDOW_TERM_CAP = 60


@dataclass(frozen=True)
class NameList:
    names: tuple[str, ...] = ()

    def block(self) -> str:
        return "\n".join(f"- {name}" for name in self.names)


def load_proper_nouns(base: Path) -> NameList:
    """Read the deterministic proper-noun list written by `shz analyze` (off mode)."""
    path = base / "artifacts" / "proper_nouns.yaml"
    if not path.is_file():
        return NameList()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid proper_noun list in {path}: {exc}") from exc
    if not isinstance(data, dict):
        return NameList()
    names: list[str] = []
    for item in data.get("names") or []:
        if isinstance(item, dict) and item.get("en"):
            names.append(str(item["en"]))
        elif isinstance(item, str):
            names.append(item)
    return NameList(tuple(dict.fromkeys(names)))


def _matches(name: str, source: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", source, re.IGNORECASE))


def match_terms(
    source_text: str, terms: Sequence[GlossaryTerm], *, cap: int = WINDOW_TERM_CAP
) -> list[GlossaryTerm]:
    """Approved terms whose EN form or alias occurs in the window source."""
    matched = [
        term
        for term in terms
        if any(_matches(name, source_text) for name in (term.en, *term.aliases_en))
    ]
    matched.sort(key=lambda term: (-len(term.en), term.en.casefold()))
    return matched[:cap]


def glossary_block(terms: Sequence[GlossaryTerm]) -> str:
    """`EN = FA` lines, longest-first, byte-stable for the cached prefix."""
    ordered = sorted(terms, key=lambda term: (-len(term.en), term.en.casefold()))
    lines: list[str] = []
    for term in ordered:
        if not term.fa:
            continue
        suffix = f" ({term.kind})" if term.kind and term.kind != "term" else ""
        lines.append(f"- {term.en} = {term.fa}{suffix}")
    return "\n".join(lines)


def anchor_block(anchors: Iterable[dict[str, Any]]) -> str:
    lines: list[str] = []
    for anchor in anchors:
        lines.append(f"[{anchor.get('id', '?')}]")
        lines.append(f"EN: {anchor.get('en', '')}")
        lines.append(f"FA: {anchor.get('fa', '')}")
        if anchor.get("note"):
            lines.append(f"note: {anchor['note']}")
        lines.append("")
    return "\n".join(lines).strip()
