"""Persian punctuation and typographic rules (N06/N07 support)."""

from __future__ import annotations

import re

PERSIAN_COMMA = "،"
PERSIAN_SEMICOLON = "؛"
PERSIAN_QUESTION = "؟"
PERSIAN_ELLIPSIS = "…"
OPEN_GUILLEMET = "«"
CLOSE_GUILLEMET = "»"

PERSIAN_LETTER_CLASS = r"\u0621-\u064A\u0671-\u06D3\u06FA-\u06FF"
_PERSIAN_LETTER = PERSIAN_LETTER_CLASS
_CONTEXTUAL_COMMA = re.compile(
    rf"(?<=[{_PERSIAN_LETTER}\u06F0-\u06F9])\s*,(?!\s*[\u06F0-\u06F9])"
)
_CONTEXTUAL_SEMICOLON = re.compile(rf"(?<=[{_PERSIAN_LETTER}])\s*;")
_CONTEXTUAL_QUESTION = re.compile(rf"(?<=[{_PERSIAN_LETTER}])\s*\?")
_PERCENT = re.compile(rf"(?<=[{_PERSIAN_LETTER}0-9\u06F0-\u06F9])٪?%")
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([،؛؟:!»…٪])")
_SPACE_AFTER_OPEN = re.compile(r"«[ \t]+")
_QUOTED = re.compile(r'"([^"\n]+)"')
_BIDI_CONTROLS = re.compile(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def convert_quotes(text: str) -> str:
    return _QUOTED.sub(rf"{OPEN_GUILLEMET}\1{CLOSE_GUILLEMET}", text)


def normalize_punctuation(text: str, *, ellipsis: str = "unicode") -> str:
    value = convert_quotes(text)
    value = _CONTEXTUAL_COMMA.sub(PERSIAN_COMMA, value)
    value = _CONTEXTUAL_SEMICOLON.sub(PERSIAN_SEMICOLON, value)
    value = _CONTEXTUAL_QUESTION.sub(PERSIAN_QUESTION, value)
    if ellipsis == "unicode":
        value = value.replace("...", PERSIAN_ELLIPSIS).replace("…", PERSIAN_ELLIPSIS)
    else:
        value = value.replace("…", "...")
    value = _PERCENT.sub("٪", value)
    value = _SPACE_BEFORE_PUNCT.sub(r"\1", value)
    value = _SPACE_AFTER_OPEN.sub(OPEN_GUILLEMET, value)
    return _BIDI_CONTROLS.sub("", value)


def guillemets_balanced(text: str) -> bool:
    return text.count(OPEN_GUILLEMET) == text.count(CLOSE_GUILLEMET)


def space_before_punctuation(text: str) -> list[str]:
    return [match.group(0) for match in _SPACE_BEFORE_PUNCT.finditer(text)]
