"""Source normalization applied before segmentation (plan 6.0.5)."""

from __future__ import annotations

import re
import unicodedata

_NBSP = "\u00a0"
_SOFT_HYPHEN = "\u00ad"
_ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"
_SPACES = re.compile(r"[ \t\f\v]+")
_TRAILING = re.compile(r"[ \t]+$", re.MULTILINE)
_MANY_NEWLINES = re.compile(r"\n{3,}")
_HYPHEN_BREAK = re.compile(r"(?<=\w)-\s*\n\s*(?=[a-z])")


def normalize_source(text: str) -> str:
    """NFC, entity cleanup, whitespace collapse, safe hyphen joins. Never edits wording."""
    if not text:
        return ""
    value = unicodedata.normalize("NFC", text)
    value = value.replace(_NBSP, " ").replace("\u202f", " ")
    for char in _SOFT_HYPHEN + _ZERO_WIDTH:
        value = value.replace(char, "")
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _HYPHEN_BREAK.sub("", value)
    value = _SPACES.sub(" ", value)
    value = _TRAILING.sub("", value)
    value = _MANY_NEWLINES.sub("\n\n", value)
    return value.strip()


def normalized_for_compare(text: str) -> str:
    return " ".join(normalize_source(text).split())
