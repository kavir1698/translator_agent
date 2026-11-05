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
