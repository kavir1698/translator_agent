"""Deterministic QA linters L01-L20 (plan section 7). Pure functions, stable codes."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from rapidfuzz import fuzz

from tarjoman.core.ids import sha256_text
from tarjoman.core.models import Severity
from tarjoman.core.tokens import count_words, sentence_count

from .markup import validate_markup
from .normalize import has_banned_codepoints, normalize_fa
from .numerals import numeric_values
from .punctuation import (
    PERSIAN_LETTER_CLASS,
    guillemets_balanced,
    space_before_punctuation,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
Scope = Literal["segment", "window", "chapter", "book"]

_ASCII_DIGITS = re.compile(r"[0-9]")
_FOREIGN_SCRIPTS = re.compile(r"[\u0370-\u03FF\u0400-\u04FF\u4E00-\u9FFF\u3040-\u30FF]")
_PERSIAN_LETTER = re.compile(r"[\u0600-\u06FF\u200c]")
_LATIN_RUN = re.compile(r"[A-Za-z][A-Za-z'’\-]*(?:\s+[A-Za-z][A-Za-z'’\-]*){3,}")
_PERSIAN_WORD = re.compile(r"[\u0600-\u06FF\u200c]{2,}")
_SENTENCE = re.compile(r"[^.!?…؛\n]+[.!?…؛]?")


@dataclass(frozen=True)
class GlossaryTerm:
    en: str
    fa: str
    kind: str = "term"
    aliases_en: tuple[str, ...] = ()
    aliases_fa: tuple[str, ...] = ()
    status: str = "approved"


@dataclass
class LintSegment:
    id: str
    kind: str = "prose"
    source_text: str = ""
    source_html: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    status: str = "done"


@dataclass
class LintTranslation:
    fa_text: str
    fa_markup: str = ""


@dataclass(frozen=True)
class LintConfig:
    digits: str = "persian"
    keep_diacritics: bool = False
    ellipsis: str = "unicode"
    zwnj_repair: str = "high_precision"


@dataclass
class LintIssue:
    code: str
    severity: str
    message: str
    quote: str = ""
    context: dict[str, Any] = field(default_factory=dict)

    def dedupe_key(self, scope: str, ref: str) -> str:
        prefix = "seg" if scope in {"segment", "window"} else "ch"
        digest = sha256_text(self.quote or self.message)[:12]
        return f"{prefix}:{ref}:{self.code}:{digest}"


@dataclass
class LintWindow:
    id: str
    segments: list[LintSegment]


@dataclass
class LintChapter:
    idx: int
    segments: list[LintSegment]
    translations: dict[str, LintTranslation]
    baseline: dict[str, float] = field(default_factory=dict)


@dataclass
class LintBook:
    segments: list[LintSegment]
    translations: dict[str, LintTranslation]
    glossary: list[GlossaryTerm] = field(default_factory=list)


@lru_cache
def _lines(name: str) -> tuple[str, ...]:
    path = DATA_DIR / name
    if not path.is_file():
        return ()
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def translationese_phrases() -> tuple[str, ...]:
    return _lines("translationese_fa.txt")


def protected_latin() -> tuple[str, ...]:
    return _lines("protected_latin.txt")


def arabic_stopwords() -> tuple[str, ...]:
    return _lines("arabic_stopwords.txt")


def zwnj_repairs() -> dict[str, str]:
    repairs: dict[str, str] = {}
    for line in _lines("zwnj_forms.txt"):
        parts = line.split("\t")
        if len(parts) == 2:
            repairs[parts[0]] = parts[1]
    return repairs


def _longest_common_substring_twice(text: str) -> bool:
    """True when any 20+ character substring repeats at least three times."""
    gram = 20
    if len(text) < gram * 3:
        return False
    counts = Counter(text[index : index + gram] for index in range(len(text) - gram + 1))
    return any(count >= 3 for count in counts.values())


def _per_1000(count: int, words: int) -> float:
    return count * 1000 / max(1, words)


def _norm_space(text: str) -> str:
    return re.sub(r"[\s\u200c]+", "", text)


def term_in_text(term: str, text: str) -> bool:
    needle = _norm_space(term)
    return bool(needle) and needle in _norm_space(text)


def _contains_source_term(term: str, source: str) -> bool:
    pattern = re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE)
    return bool(pattern.search(source))


def _check_l01(seg: LintSegment, tr: LintTranslation | None) -> list[LintIssue]:
    if seg.status == "skipped":
        return []
    if tr is None or not tr.fa_text.strip():
        return [LintIssue("L01", Severity.ERROR, "segment has no translation")]
    if not _PERSIAN_LETTER.search(tr.fa_text):
        return [LintIssue("L01", Severity.ERROR, "translation contains no Persian letters")]
    return []


def _check_l02(seg: LintSegment, tr: LintTranslation) -> list[LintIssue]:
    if not seg.nodes or not tr.fa_markup:
        return []
    return [
        LintIssue("L02", Severity.ERROR, f"markup invalid: {problem.message}", quote=tr.fa_markup[:80])
        for problem in validate_markup(tr.fa_markup, seg.nodes)
    ]


def _check_l04(tr: LintTranslation) -> list[LintIssue]:
    issues: list[LintIssue] = []
    text = tr.fa_text
    banned = sorted(set(has_banned_codepoints(text)))
    if banned:
        issues.append(
            LintIssue("L04", Severity.ERROR, f"banned Arabic code points: {banned}", quote=banned[0])
        )
    if _FOREIGN_SCRIPTS.search(text):
        issues.append(LintIssue("L04", Severity.ERROR, "non-Persian script letters", quote=""))
    hits = [word for word in arabic_stopwords() if _contains_source_term(word, text)]
    if hits:
        issues.append(
            LintIssue("L04", Severity.ERROR, f"Arabic-only vocabulary: {hits[:3]}", quote=hits[0])
        )
    if "ة" in text:
        issues.append(
            LintIssue("L04", Severity.WARN, "Arabic teh marbuta needs a lexical decision", quote="ة")
        )
    return issues


def _check_l05(seg: LintSegment, tr: LintTranslation, config: LintConfig) -> list[LintIssue]:
    issues: list[LintIssue] = []
    if config.digits == "persian" and _ASCII_DIGITS.search(tr.fa_text):
        issues.append(
            LintIssue("L05", Severity.ERROR, "ASCII digits in a Persian run", quote=tr.fa_text[:60])
        )
    source_numbers = numeric_values(seg.source_text)
    if source_numbers:
        target_numbers = numeric_values(tr.fa_text)
        if sorted(source_numbers) != sorted(target_numbers):
            issues.append(
                LintIssue(
                    "L05",
                    Severity.ERROR,
                    f"number mismatch: source {source_numbers} target {target_numbers}",
                    quote=tr.fa_text[:80],
                )
            )
    return issues


def _check_l06(tr: LintTranslation) -> list[LintIssue]:
    issues: list[LintIssue] = []
    text = tr.fa_text
    if re.search(rf"(?<=[{PERSIAN_LETTER_CLASS}])\s*[,;?]", text):
        issues.append(
            LintIssue("L06", Severity.WARN, "ASCII punctuation after Persian text", quote=text[:60])
        )
    if "..." in text:
        issues.append(LintIssue("L06", Severity.WARN, "ASCII ellipsis", quote="..."))
    if not guillemets_balanced(text):
        issues.append(LintIssue("L06", Severity.WARN, "unbalanced guillemets", quote="«»"))
    if space_before_punctuation(text):
        issues.append(
            LintIssue("L06", Severity.WARN, "space before Persian punctuation", quote=text[:60])
        )
    if "  " in text:
        issues.append(LintIssue("L06", Severity.WARN, "double space", quote=text[:60]))
    return issues


def _check_l07(tr: LintTranslation) -> list[LintIssue]:
    text = tr.fa_text
    patterns = {
        "می + space": re.compile(r"(?:^|\s)می\s+[\u0600-\u06FF]"),
        "ها separated": re.compile(r"[\u0600-\u06FF]\s+های?(?:\s|$|[،؛.])"),
        "تر separated": re.compile(r"[\u0600-\u06FF]\s+تری?ن?(?:\s|$|[،؛.])"),
        "ZWNJ at edge": re.compile(r"(?:^|[\s(«])‌|‌(?:$|[\s)»،؛.])"),
        "double ZWNJ": re.compile("‌‌"),
    }
    issues = []
    for label, pattern in patterns.items():
        match = pattern.search(text)
        if match:
            issues.append(LintIssue("L07", Severity.WARN, f"ZWNJ: {label}", quote=match.group(0)))
    return issues


def _check_l08(seg: LintSegment, tr: LintTranslation) -> list[LintIssue]:
    source_words = max(1, count_words(seg.source_text))
    target_words = count_words(tr.fa_text)
    ratio = target_words / source_words
    issues: list[LintIssue] = []
    severity = Severity.WARN
    if ratio < 0.55 or ratio > 1.70:
        severity = Severity.ERROR
    if ratio < 0.75 or ratio > 1.30:
        issues.append(
            LintIssue(
                "L08",
                severity,
                f"word ratio {ratio:.2f} outside the {'hard' if severity == Severity.ERROR else 'soft'} band",
                context={"ratio": ratio},
            )
        )
    source_sentences = max(1, sentence_count(seg.source_text))
    target_sentences = max(1, sentence_count(tr.fa_text))
    sentence_ratio = target_sentences / source_sentences
    if sentence_ratio < 0.60 or sentence_ratio > 1.80:
        issues.append(
            LintIssue(
                "L08",
                Severity.WARN,
                f"sentence ratio {sentence_ratio:.2f} outside [0.60, 1.80]",
                context={"sentence_ratio": sentence_ratio},
            )
        )
    return issues


def _is_protected(run: str) -> bool:
    lowered = run.lower()
    tokens = {token.lower() for token in re.findall(r"[A-Za-z0-9]+", run)}
    return any(word.lower() in tokens for word in protected_latin()) or bool(
        re.search(r"https?://|www\.|\.(com|org|net)\b", lowered)
    )


def _check_l10(tr: LintTranslation) -> list[LintIssue]:
    issues = []
    for match in _LATIN_RUN.finditer(tr.fa_text):
        run = match.group(0)
        if not _is_protected(run):
            issues.append(
                LintIssue("L10", Severity.ERROR, "untranslated Latin run", quote=run[:80])
            )
    return issues


def _check_l11(
    seg: LintSegment, tr: LintTranslation, glossary: list[GlossaryTerm]
) -> list[LintIssue]:
    issues = []
    for term in glossary:
        if term.status != "approved":
            continue
        names = (term.en, *term.aliases_en)
        if any(_contains_source_term(name, seg.source_text) for name in names):
            forms = (term.fa, *term.aliases_fa)
            if not any(term_in_text(form, tr.fa_text) for form in forms):
                issues.append(
                    LintIssue(
                        "L11",
                        Severity.WARN,
                        f"approved term {term.en!r} missing from the translation",
                        quote=term.fa,
                    )
                )
    return issues


def _check_l13(tr: LintTranslation) -> list[LintIssue]:
    patterns = (
        "as an ai",
        "i can't",
        "i cannot",
        "i'm sorry",
        "متأسفم",
        "به عنوان هوش مصنوعی",
    )
    lowered = tr.fa_text.lower()
    hits = [pattern for pattern in patterns if pattern in lowered]
    if hits:
        return [
            LintIssue("L13", Severity.CRITICAL, f"refusal pattern: {hits[0]!r}", quote=tr.fa_text[:80])
        ]
    return []


def _check_l14(tr: LintTranslation) -> list[LintIssue]:
    text = tr.fa_text
    words = max(1, count_words(text))
    issues: list[LintIssue] = []
    blacklist = [phrase for phrase in translationese_phrases() if phrase in text]
    if blacklist:
        issues.append(
            LintIssue(
                "L14",
                Severity.WARN,
                f"translationese phrases: {blacklist[:4]}",
                quote=blacklist[0],
            )
        )
    mibashad = text.count("می‌باشد") + text.count("می باشد")
    if mibashad:
        issues.append(LintIssue("L14", Severity.WARN, "می‌باشد/می باشد is bureaucratic", quote="می‌باشد"))
    if words < 100:
        return issues
    passive = len(re.findall(r"گردید|گردند|گردد", text))
    if _per_1000(passive, words) > 2:
        issues.append(LintIssue("L14", Severity.WARN, f"passive گردید density {passive/words*1000:.1f}/1k"))
    ke = len(re.findall(r"(?<![\u0600-\u06FF])که(?![\u0600-\u06FF])", text))
    if _per_1000(ke, words) > 60:
        issues.append(LintIssue("L14", Severity.WARN, f"که density {ke/words*1000:.1f}/1k"))
    sentences = [part.strip() for part in _SENTENCE.findall(text) if part.strip()]
    if sentences:
        initial_va = sum(1 for sentence in sentences if sentence.startswith("و"))
        if initial_va / len(sentences) > 0.30:
            issues.append(
                LintIssue(
                    "L14",
                    Severity.WARN,
                    f"sentence-initial و ratio {initial_va/len(sentences):.0%}",
                )
            )
    ra = len(re.findall(r"(?<![\u0600-\u06FF])را(?![\u0600-\u06FF])", text))
    if _per_1000(ra, words) < 2 or _per_1000(ra, words) > 40:
        issues.append(LintIssue("L14", Severity.WARN, f"را density {ra/words*1000:.1f}/1k"))
    return issues


def _check_l16(seg: LintSegment, tr: LintTranslation) -> list[LintIssue]:
    issues = []
    meta = re.search(r"ترجمه:|\(مترجم\)|Here is|Note:|در اینجا", tr.fa_text)
    if meta:
        issues.append(
            LintIssue("L16", Severity.CRITICAL, "translator meta-commentary", quote=meta.group(0))
        )
    ratio = count_words(tr.fa_text) / max(1, count_words(seg.source_text))
    if ratio > 1.8:
        issues.append(
            LintIssue("L16", Severity.CRITICAL, f"translation is {ratio:.1f}x the source length")
        )
    return issues


def _check_l17(seg: LintSegment, tr: LintTranslation) -> list[LintIssue]:
    if seg.kind != "verse":
        return []
    source_lines = max(1, seg.source_text.count("\n") + 1)
    target_lines = max(1, tr.fa_text.count("\n") + 1)
    if abs(source_lines - target_lines) > 1:
        return [
            LintIssue(
                "L17",
                Severity.WARN,
                f"verse line count {target_lines} vs source {source_lines}",
            )
        ]
    return []


def _check_l20(seg: LintSegment, tr: LintTranslation) -> list[LintIssue]:
    issues = []
    if seg.kind == "heading":
        source_ends = seg.source_text.rstrip().endswith((".", "!", "?", ":", "؛", "…"))
        target_ends = tr.fa_text.rstrip().endswith((".", "!", "?", ":", "؛", "…"))
        if target_ends and not source_ends:
            issues.append(
                LintIssue("L20", Severity.WARN, "heading punctuated as a sentence", quote=tr.fa_text)
            )
    if seg.kind in {"caption", "table"} and not tr.fa_text.strip():
        issues.append(LintIssue("L20", Severity.WARN, f"empty {seg.kind} translation"))
    return issues


def check_segment(
    seg: LintSegment,
    tr: LintTranslation | None,
    glossary: list[GlossaryTerm] | None = None,
    config: LintConfig | None = None,
) -> list[LintIssue]:
    settings = config or LintConfig()
    issues = _check_l01(seg, tr)
    if tr is None:
        return issues
    if not tr.fa_text.strip():
        issues += _check_l20(seg, tr)
        return issues
    issues += _check_l02(seg, tr)
    issues += _check_l04(tr)
    issues += _check_l05(seg, tr, settings)
    issues += _check_l06(tr)
    issues += _check_l07(tr)
    issues += _check_l08(seg, tr)
    issues += _check_l10(tr)
    issues += _check_l11(seg, tr, glossary or [])
    issues += _check_l13(tr)
    issues += _check_l14(tr)
    issues += _check_l16(seg, tr)
    issues += _check_l17(seg, tr)
    issues += _check_l20(seg, tr)
    return issues


def check_window(
    window: LintWindow,
    trs: dict[str, LintTranslation],
    glossary: list[GlossaryTerm] | None = None,
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    source_text = " ".join(segment.source_text for segment in window.segments)
    target_text = " ".join(
        trs[segment.id].fa_text for segment in window.segments if segment.id in trs
    )
    for term in glossary or []:
        if term.status != "approved":
            continue
        names = (term.en, *term.aliases_en)
        if any(_contains_source_term(name, source_text) for name in names):
            forms = (term.fa, *term.aliases_fa)
            if not any(term_in_text(form, target_text) for form in forms):
                issues.append(
                    LintIssue(
                        "L03",
                        Severity.ERROR,
                        f"approved term {term.en!r} missing in window {window.id}",
                        quote=term.fa,
                    )
                )
    expected = sum(1 for segment in window.segments if segment.status != "skipped")
    if len(trs) < expected:
        issues.append(
            LintIssue("L18", Severity.ERROR, f"window has {len(trs)} translations for {expected} segments")
        )
    return issues


def _chapter_metrics(segments: list[LintSegment], trs: dict[str, LintTranslation]) -> dict[str, float]:
    texts = [trs[segment.id].fa_text for segment in segments if segment.id in trs]
    if not texts:
        return {}
    words = sum(count_words(text) for text in texts)
    sentences = sum(max(1, sentence_count(text)) for text in texts)
    unique = len({word for text in texts for word in text.split()})
    return {
        "mean_sentence_words": words / max(1, sentences),
        "dialogue_ratio": sum(text.count("«") for text in texts) / max(1, len(texts)),
        "unique_word_ratio": unique / max(1, words),
    }


def check_chapter(
    chapter: LintChapter,
    segs: list[LintSegment] | None = None,
    trs: dict[str, LintTranslation] | None = None,
) -> list[LintIssue]:
    segments = segs if segs is not None else chapter.segments
    translations = trs if trs is not None else chapter.translations
    issues: list[LintIssue] = []

    chapter_html = " ".join(segment.source_html for segment in segments)
    ids = set(re.findall(r'\bid="([^"]+)"', chapter_html))
    for target in re.findall(r'href="#([^"]+)"', chapter_html):
        if target and target not in ids:
            issues.append(
                LintIssue("L19", Severity.ERROR, f"dangling footnote reference #{target}", quote=target)
            )
    for segment in segments:
        if segment.kind == "footnote" and not translations.get(segment.id):
            issues.append(
                LintIssue("L19", Severity.ERROR, f"footnote {segment.id} is not translated")
            )

    if chapter.baseline:
        metrics = _chapter_metrics(segments, translations)
        deviations = [
            name
            for name, baseline in chapter.baseline.items()
            if name in metrics and abs(metrics[name] - baseline) > max(0.25 * abs(baseline), 0.05)
        ]
        if len(deviations) >= 2:
            issues.append(
                LintIssue(
                    "L15",
                    Severity.WARN,
                    f"chapter metrics drift from the running baseline: {deviations}",
                    context={"metrics": metrics},
                )
            )
    return issues


def _sentence_list(text: str) -> list[str]:
    return [
        normalize_fa(part).strip()
        for part in re.split(r"[.!?…؛\n]+", text)
        if len(part.strip()) > 8
    ]


def check_book(book: LintBook) -> list[LintIssue]:
    issues: list[LintIssue] = []
    sentence_counts: Counter[str] = Counter()
    for segment in book.segments:
        translation = book.translations.get(segment.id)
        if translation is None:
            continue
        for sentence in _sentence_list(translation.fa_text):
            sentence_counts[sentence] += 1
        if _longest_common_substring_twice(translation.fa_text):
            issues.append(
                LintIssue(
                    "L09",
                    Severity.WARN,
                    f"repeated 20-character substring in {segment.id}",
                    quote=translation.fa_text[:60],
                    context={"segment_id": segment.id},
                )
            )
    for sentence, count in sentence_counts.items():
        if count > 2:
            issues.append(
                LintIssue("L09", Severity.WARN, f"sentence appears {count} times", quote=sentence[:80])
            )

    approved = [term for term in book.glossary if term.status == "approved"]
    for term in approved:
        if term.kind not in {"character", "place", "organization", "motif"}:
            continue
        approved_forms = {_norm_space(term.fa), *(_norm_space(alias) for alias in term.aliases_fa)}
        for segment in book.segments:
            translation = book.translations.get(segment.id)
            if translation is None:
                continue
            for token in _PERSIAN_WORD.findall(translation.fa_text):
                normalized = _norm_space(token)
                if normalized in approved_forms:
                    continue
                if any(fuzz.ratio(normalized, form) >= 88 for form in approved_forms):
                    issues.append(
                        LintIssue(
                            "L12",
                            Severity.WARN,
                            f"unapproved variant of {term.en!r}: {token}",
                            quote=token,
                            context={"segment_id": segment.id},
                        )
                    )

    source_counts: Counter[str] = Counter()
    for segment in book.segments:
        for token in re.findall(r"\b[A-Z][a-z]{2,}\b", segment.source_text):
            source_counts[token] += 1
    known = {term.en.casefold() for term in approved}
    for token, count in source_counts.items():
        if count >= 3 and token.casefold() not in known:
            issues.append(
                LintIssue(
                    "L12",
                    Severity.WARN,
                    f"recurring proper noun {token!r} ({count}x) has no glossary entry",
                    quote=token,
                )
            )
    return issues


CHECK_REGISTRY: dict[str, dict[str, str]] = {
    "L01": {"scope": "segment", "severity": "error", "name": "coverage"},
    "L02": {"scope": "segment", "severity": "error", "name": "markup integrity"},
    "L03": {"scope": "window", "severity": "error", "name": "glossary presence"},
    "L04": {"scope": "segment", "severity": "error", "name": "script purity"},
    "L05": {"scope": "segment", "severity": "error", "name": "digits and numerals"},
    "L06": {"scope": "segment", "severity": "warn", "name": "punctuation"},
    "L07": {"scope": "segment", "severity": "warn", "name": "ZWNJ"},
    "L08": {"scope": "segment", "severity": "warn", "name": "length drift"},
    "L09": {"scope": "book", "severity": "warn", "name": "repetition"},
    "L10": {"scope": "segment", "severity": "error", "name": "residual source"},
    "L11": {"scope": "segment", "severity": "warn", "name": "entity carry-over"},
    "L12": {"scope": "book", "severity": "warn", "name": "name variants"},
    "L13": {"scope": "segment", "severity": "critical", "name": "refusal"},
    "L14": {"scope": "segment", "severity": "warn", "name": "translationese"},
    "L15": {"scope": "chapter", "severity": "warn", "name": "style drift"},
    "L16": {"scope": "segment", "severity": "critical", "name": "meta-commentary"},
    "L17": {"scope": "segment", "severity": "warn", "name": "verse structure"},
    "L18": {"scope": "window", "severity": "error", "name": "block structure"},
    "L19": {"scope": "chapter", "severity": "error", "name": "footnote integrity"},
    "L20": {"scope": "segment", "severity": "warn", "name": "heading/caption sanity"},
}


def auto_fix(text: str, *, config: LintConfig | None = None) -> str:
    """Fix only mechanically certain classes: code points, digits, punctuation, ZWNJ list."""
    settings = config or LintConfig()
    value = normalize_fa(
        text,
        digits=settings.digits,
        keep_diacritics=settings.keep_diacritics,
        ellipsis=settings.ellipsis,
    )
    if settings.zwnj_repair != "off":
        for joined, corrected in zwnj_repairs().items():
            value = re.sub(rf"(?<![\u0600-\u06FF]){re.escape(joined)}(?![\u0600-\u06FF])", corrected, value)
    return value
