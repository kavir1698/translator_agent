"""Persian normalization N01-N09 (plan 7.3). Pure functions, table-driven, idempotent."""

from __future__ import annotations

import re
import unicodedata

from .numerals import convert_digits
from .punctuation import normalize_punctuation

CHAR_MAP = {
    "\u064a": "\u06cc",  # Arabic yeh -> Persian yeh
    "\u0649": "\u06cc",  # alef maksura -> Persian yeh
    "\u0643": "\u06a9",  # Arabic kaf -> Persian kaf
    "\u0640": "",  # tatweel
    "\u200b": "",  # zero-width space
    "\u200e": "",  # LRM
    "\u200f": "",  # RLM
    "\u00a0": " ",
    "\u202f": " ",
}
_MAP_TABLE = str.maketrans(CHAR_MAP)
_HARAKAT = re.compile(r"[\u064B-\u0655]")
_ZWNJ = "\u200c"
_ZWNJ_RUN = re.compile(r"\u200c{2,}")
_ZWNJ_EDGE = re.compile(r"(?<![\w\u0600-\u06FF])\u200c|\u200c(?![\w\u0600-\u06FF])")
_ZWNJ_SPACE = re.compile(r"[ \t]+\u200c[ \t]*|[ \t]*\u200c[ \t]+")
_SPACES = re.compile(r"[ \t]+")
_TRAILING = re.compile(r"[ \t]+$", re.MULTILINE)
_MANY_LINES = re.compile(r"\n{3,}")
_DECIMAL = re.compile(r"(?<=[\u06F0-\u06F9])\.")
_THOUSANDS = re.compile(r"(?<=[\u06F0-\u06F9]),(?=[\u06F0-\u06F9])")


def _n01(text: str) -> str:
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFC", text))


def _n02(text: str) -> str:
    return text.translate(_MAP_TABLE)


def _n04(text: str, keep: bool) -> str:
    return text if keep else _HARAKAT.sub("", text)


def _n05(text: str) -> str:
    value = _ZWNJ_RUN.sub(_ZWNJ, text)
    value = _ZWNJ_SPACE.sub(" ", value)
    return _ZWNJ_EDGE.sub("", value)


def _n07(text: str, convert_separators: bool) -> str:
    value = text.replace("%", "٪") if "%" in text else text
    value = re.sub(r"[ \t]+٪", "٪", value)
    if convert_separators:
        value = _DECIMAL.sub("٫", value)
        value = _THOUSANDS.sub("٬", value)
    return value


def _n08(text: str, *, strip_edges: bool) -> str:
    value = text.replace("\r\n", "\n").replace("\r", "\n")
    value = _TRAILING.sub("", value)
    value = _MANY_LINES.sub("\n\n", value)
    value = _SPACES.sub(" ", value)
    return value.strip() if strip_edges else value


def normalize_fa(
    text: str,
    *,
    digits: str = "persian",
    keep_diacritics: bool = False,
    ellipsis: str = "unicode",
    convert_number_separators: bool = False,
    strip_edges: bool = True,
) -> str:
    """Normalize Persian text. Safe to call twice: `normalize_fa(normalize_fa(x)) == normalize_fa(x)`."""
    if not text:
        return ""
    value = _n01(text)
    value = _n02(value)
    value = convert_digits(value, digits)
    value = _n04(value, keep_diacritics)
    value = _n05(value)
    value = normalize_punctuation(value, ellipsis=ellipsis)
    value = _n07(value, convert_number_separators)
    value = _n08(value, strip_edges=strip_edges)
    return unicodedata.normalize("NFC", value)


def has_banned_codepoints(text: str) -> list[str]:
    banned: list[str] = []
    for char in text:
        code = ord(char)
        if char in {"\u064a", "\u0649", "\u0643", "\u0640"} or 0xFB50 <= code <= 0xFDFF or 0xFE70 <= code <= 0xFEFF or 0x0660 <= code <= 0x0669:
            banned.append(char)
    return banned
