"""Deterministic English sentence splitting and source style metrics (plan 6.1.2)."""

from __future__ import annotations

import re
from collections.abc import Sequence
from statistics import mean, median
from typing import Any

from .tokens import count_words, dialogue_ratio

_WS = re.compile(r"\s+")
_SENTENCE_END = re.compile(r"[.!?…]+[\"'»”’)]*(?=\s|$)")
_WORD = re.compile(r"[A-Za-z\u00C0-\u024F]+(?:['’\-][A-Za-z\u00C0-\u024F]+)*")
_LAST_TOKEN = re.compile(r"(?:^|\s)([A-Za-z.]+)['\"»”’)]*$")

_ABBREVIATIONS = {
    "mr",
    "mrs",
    "ms",
    "dr",
    "prof",
    "st",
    "jr",
    "sr",
    "vs",
    "etc",
    "e.g",
    "i.e",
    "a.m",
    "p.m",
    "no",
    "vol",
    "ch",
    "p",
    "pp",
}


def _ends_with_abbreviation(chunk: str) -> bool:
    match = _LAST_TOKEN.search(chunk.strip())
    if match is None:
        return False
    token = match.group(1)
    lowered = token.lower()
    if lowered in _ABBREVIATIONS:
        return True
    if lowered.rstrip(".") in _ABBREVIATIONS:
        return True
    return bool(re.fullmatch(r"(?:[A-Za-z]\.)+", token))


def split_sentences_en(text: str) -> list[str]:
    """Split English prose without breaking on common abbreviations or initials."""
    normalized = _WS.sub(" ", text.strip())
    if not normalized:
        return []
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(normalized):
        chunk = normalized[start : match.end()].strip()
        if not chunk or _ends_with_abbreviation(chunk):
            continue
        sentences.append(chunk)
        start = match.end()
    tail = normalized[start:].strip()
    if tail:
        if sentences:
            sentences[-1] = f"{sentences[-1]} {tail}".strip()
        else:
            sentences.append(tail)
    return sentences or [normalized]


def _distribution(lengths: Sequence[int]) -> dict[str, int]:
    buckets = {
        "1-9": 0,
        "10-19": 0,
        "20-29": 0,
        "30-39": 0,
        "40-49": 0,
        "50+": 0,
    }
    for length in lengths:
        if length < 10:
            buckets["1-9"] += 1
        elif length < 20:
            buckets["10-19"] += 1
        elif length < 30:
            buckets["20-29"] += 1
        elif length < 40:
            buckets["30-39"] += 1
        elif length < 50:
            buckets["40-49"] += 1
        else:
            buckets["50+"] += 1
    return buckets


def _per_1k(count: int, words: int) -> float:
    return round(count * 1000 / max(1, words), 3)


def compute_style_metrics(paragraphs: Sequence[str]) -> dict[str, Any]:
    """Deterministic source-side metrics stored in ``books.source_metrics_json``."""
    texts = [text for text in paragraphs if text and text.strip()]
    full = " ".join(texts)
    words = count_words(full)
    sentences = [
        sentence
        for text in texts
        for sentence in split_sentences_en(text)
        if sentence.strip()
    ]
    sentence_words = [count_words(sentence) for sentence in sentences]
    paragraph_words = [count_words(text) for text in texts]
    tokens = [match.group(0).casefold() for match in _WORD.finditer(full)]

    commas = full.count(",")
    semicolons = full.count(";")
    em_dashes = full.count("—") + full.count("–")
    questions = full.count("?")
    exclamations = full.count("!")

    return {
        "words": words,
        "sentences": len(sentences),
        "paragraphs": len(texts),
        "mean_sentence_words": round(mean(sentence_words), 3) if sentence_words else 0.0,
        "median_sentence_words": round(median(sentence_words), 3) if sentence_words else 0.0,
        "max_sentence_words": max(sentence_words) if sentence_words else 0,
        "sentence_length_distribution": _distribution(sentence_words),
        "mean_paragraph_words": round(mean(paragraph_words), 3) if paragraph_words else 0.0,
        "median_paragraph_words": round(median(paragraph_words), 3) if paragraph_words else 0.0,
        "dialogue_ratio": round(dialogue_ratio(full), 3),
        "unique_word_ratio": round(len(set(tokens)) / max(1, len(tokens)), 3),
        "punctuation_per_1k": {
            "comma": _per_1k(commas, words),
            "semicolon": _per_1k(semicolons, words),
            "em_dash": _per_1k(em_dashes, words),
            "question": _per_1k(questions, words),
            "exclamation": _per_1k(exclamations, words),
        },
        "em_dash_per_1k": _per_1k(em_dashes, words),
        "semicolon_per_1k": _per_1k(semicolons, words),
    }
