"""Persian digits and number handling."""

from __future__ import annotations

import re

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_INDIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
ASCII_DIGITS = "0123456789"

_TO_PERSIAN = str.maketrans(ASCII_DIGITS + ARABIC_INDIC_DIGITS, PERSIAN_DIGITS * 2)
_TO_ASCII = str.maketrans(PERSIAN_DIGITS + ARABIC_INDIC_DIGITS, ASCII_DIGITS * 2)
_DIGITS = re.compile(r"[0-9\u06F0-\u06F9\u0660-\u0669]+(?:[.,٫٬][0-9\u06F0-\u06F9\u0660-\u0669]+)*")


def to_persian_digits(text: str) -> str:
    return text.translate(_TO_PERSIAN)


def to_ascii_digits(text: str) -> str:
    return text.translate(_TO_ASCII)


def convert_digits(text: str, mode: str = "persian") -> str:
    if mode == "persian":
        return to_persian_digits(text)
    if mode == "ascii":
        return to_ascii_digits(text)
    return text


def parse_number(token: str) -> float | None:
    value = to_ascii_digits(token)
    value = value.replace("٬", "").replace(",", "").replace("٫", ".")
    try:
        return float(value)
    except ValueError:
        return None


def extract_numbers(text: str) -> list[tuple[str, float]]:
    numbers: list[tuple[str, float]] = []
    for match in _DIGITS.finditer(text):
        token = match.group(0)
        parsed = parse_number(token)
        if parsed is not None:
            numbers.append((token, parsed))
    return numbers


def numeric_values(text: str) -> list[float]:
    return [value for _, value in extract_numbers(text)]


def numbers_match(source: str, target: str) -> bool:
    return sorted(numeric_values(source)) == sorted(numeric_values(target))


_ONES = ("صفر", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه")
_TEENS = (
    "ده",
    "یازده",
    "دوازده",
    "سیزده",
    "چهارده",
    "پانزده",
    "شانزده",
    "هفده",
    "هجده",
    "نوزده",
)
_TENS = {20: "بیست", 30: "سی", 40: "چهل", 50: "پنجاه", 60: "شصت", 70: "هفتاد", 80: "هشتاد", 90: "نود"}
_JOINERS = re.compile(r"[\s\u200c\u200e\u200f]+")


def _fa_cardinal(value: int) -> str | None:
    if 0 <= value < 10:
        return _ONES[value]
    if 10 <= value < 20:
        return _TEENS[value - 10]
    if value < 100:
        tens, ones = divmod(value, 10)
        word = _TENS[tens * 10]
        return word if ones == 0 else f"{word} و {_ONES[ones]}"
    return None


def spelled_number_present(value: float, text: str) -> bool:
    """True when ``value`` appears in ``text`` as a Persian number word (incl. ordinals)."""
    if value != int(value) or value < 0:
        return False
    word = _fa_cardinal(int(value))
    if word is None:
        return False
    return _JOINERS.sub("", word) in _JOINERS.sub("", text)
