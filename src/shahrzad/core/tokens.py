"""Token estimation (English source, Persian target) and character statistics."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

EN_TOKENS_PER_CHAR = 0.25
FA_TOKENS_PER_CHAR = 0.45

_LATIN = re.compile(r"[A-Za-z\u00C0-\u024F]")
_PERSIAN = re.compile(r"[\u0600-\u06FF]")
_DIGIT = re.compile(r"[0-9\u06F0-\u06F9\u0660-\u0669]")
_SENTENCE_END = re.compile(r"[.!?…]+[\"'»”’)]*\s+|\n+")


@dataclass(frozen=True)
class TokenRatios:
    """Tokens-per-character calibrations, stored per book once measured."""

    en_tokens_per_char: float = EN_TOKENS_PER_CHAR
    fa_tokens_per_char: float = FA_TOKENS_PER_CHAR


def estimate_tokens_en(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) * EN_TOKENS_PER_CHAR))


def estimate_tokens_fa(text: str, ratios: TokenRatios | None = None) -> int:
    if not text:
        return 0
    ratio = (ratios or TokenRatios()).fa_tokens_per_char
    return max(1, math.ceil(len(text) * ratio))


def count_words(text: str) -> int:
    return len([token for token in re.split(r"\s+", text.strip()) if token])


def count_persian_letters(text: str) -> int:
    return len(_PERSIAN.findall(text))


def count_latin_letters(text: str) -> int:
    return len(_LATIN.findall(text))


def lang_mix(text: str) -> float:
    """Share of letters that are non-Latin; > 0.3 means foreign-content treatment."""
    latin = count_latin_letters(text)
    persian = count_persian_letters(text)
    total = latin + persian
    if total == 0:
        return 0.0
    return persian / total


def sentence_count(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    parts = _SENTENCE_END.split(stripped)
    return max(1, len([part for part in parts if part.strip()]))


def dialogue_ratio(text: str) -> float:
    """Share of characters inside quotation marks, ASCII or Persian."""
    if not text:
        return 0.0
    quoted = sum(len(match) for match in re.findall(r"\"[^\"]*\"|«[^»]*»|“[^”]*”", text))
    return quoted / len(text)


def digit_count(text: str) -> int:
    return len(_DIGIT.findall(text))
