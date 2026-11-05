"""`tj qa`: deterministic linters, risk scores, flags.csv and issue lifecycle (plan 7)."""

from __future__ import annotations

import csv
import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tarjoman.core.config import Settings
from tarjoman.core.db import Database
from tarjoman.core.errors import ConfigError, RuntimeFailure
from tarjoman.core.ids import sha256_text
from tarjoman.core.models import SegmentStatus, Severity
from tarjoman.core.paths import find_book_dir
from tarjoman.core.tokens import count_words
from tarjoman.persian.lint import (
    GlossaryTerm,
    LintBook,
    LintChapter,
    LintConfig,
    LintIssue,
    LintSegment,
    LintTranslation,
    LintWindow,
    check_book,
    check_chapter,
    check_segment,
    check_window,
)

from .glossary_ops import approved_terms
from .risk import compute_risk
from .window import plan_windows

GROUPS = {
    "consistency": {"L03", "L11", "L12"},
    "literary": {"L08", "L14", "L15"},
    "loss": {"L02", "L10", "L16", "L19"},
}


@dataclass
class QaOptions:
    chapter: int | None = None
    group: str | None = None
    stale_glossary: bool = False
    json_output: bool = False
    preserve_flags: bool = False


@dataclass
class QaResult:
    issues: int = 0
    errors: int = 0
    criticals: int = 0
    coverage: float = 0.0
    flags_path: Path | None = None
    flags: int = 0
    stale: int = 0
    book_slug: str = ""
    groups: dict[str, int] = field(default_factory=dict)
    rows: list[dict[str, Any]] = field(default_factory=list)


def _nodes(segment: Any) -> list[dict[str, Any]]:
    raw = segment["nodes_json"]
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
    return list(raw or [])


def _lint_config(settings: Settings) -> LintConfig:
    return LintConfig(
        digits=settings.persian.digits,
        keep_diacritics=settings.persian.keep_diacritics,
        ellipsis=settings.persian.ellipsis,
        zwnj_repair=settings.persian.zwnj_repair,
    )


def _lint_objects(
    segments: list[Any], translations: dict[str, Any]
) -> tuple[list[LintSegment], dict[str, LintTranslation]]:
    lint_segments = [
        LintSegment(
            id=str(segment["id"]),
            kind=str(segment["kind"]),
            source_text=str(segment["source_text"]),
            source_html=str(segment["source_html"]),
            nodes=_nodes(segment),
            status=str(segment["status"]),
        )
        for segment in segments
    ]
    lint_translations = {
        key: LintTranslation(fa_text=str(row["fa_text"]), fa_markup=str(row["fa_markup"]))
        for key, row in translations.items()
    }
    return lint_segments, lint_translations


def _glossary_digest(terms: list[GlossaryTerm]) -> str:
    payload = json.dumps(
        [[term.en, term.fa, term.kind, list(term.aliases_en), list(term.aliases_fa)] for term in terms],
        sort_keys=True,
        ensure_ascii=False,
    )
    return f"sha256:{sha256_text(payload)}"


def _record(
    db: Database,
    issue: LintIssue,
    *,
    scope: str,
    ref: str,
    chapter_id: int | None,
    segment_id: str | None,
) -> str:
    target = issue.context.get("segment_id") if issue.context else None
    key = issue.dedupe_key(scope, ref)
    db.upsert_issue(
        stage="qa",
        code=issue.code,
        dedupe_key=key,
        severity=str(issue.severity),
        message=issue.message,
        segment_id=segment_id or target,
        chapter_id=chapter_id,
        quote_source=issue.quote,
        context=issue.context,
    )
    return key


def _resolve_stale(
    db: Database, book_id: int, keep: set[str], chapter_ids: list[int] | None = None
) -> None:
    params: list[Any] = [book_id]
    clause = ""
    if chapter_ids:
        marks = ",".join("?" for _ in chapter_ids)
        clause = f" AND c.id IN ({marks})"
        params.extend(chapter_ids)
    for row in db.fetchall(
        f"""
        SELECT i.id, i.dedupe_key FROM issues i
        LEFT JOIN segments s ON s.id = i.segment_id
        JOIN chapters c ON c.id = COALESCE(i.chapter_id, s.chapter_id)
        WHERE i.stage = 'qa' AND i.status = 'open' AND i.code NOT LIKE 'REVIEW_%'
              AND c.book_id = ?{clause}
        """,
        tuple(params),
    ):
        if str(row["dedupe_key"]) not in keep:
            db.set_issue_status(int(row["id"]), "resolved")


def _window_issue_counts(db: Database, windows: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for window in windows:
        marks = ",".join("?" for _ in window.segment_ids)
        if not window.segment_ids:
            continue
        row = db.fetchone(
            f"SELECT COUNT(*) FROM issues WHERE status = 'open' AND segment_id IN ({marks})",
            tuple(window.segment_ids),
        )
        count = int(row[0]) if row else 0
        for segment_id in window.segment_ids:
            counts[segment_id] = count
    return counts


def run_qa(
    *,
    root: Path,
    slug: str | None,
    settings: Settings,
    options: QaOptions | None = None,
) -> QaResult:
    options = options or QaOptions()
    if options.group and options.group not in GROUPS and options.group != "all":
        raise ConfigError(
            f"unknown qa group {options.group!r}",
            hint=f"use one of: all, {', '.join(sorted(GROUPS))}",
        )
    base = find_book_dir(root, slug, settings.project.books_dir)
    db = Database(base / "book.db")
    db.initialize()
    try:
        book = db.fetchone("SELECT * FROM books LIMIT 1")
        if book is None:
            raise ConfigError("the workspace has no book row", hint="re-run `tj init`")
        book_id = int(book["id"])
        chapters = db.chapters(book_id)
        if options.chapter is not None:
            chapters = [chapter for chapter in chapters if int(chapter["idx"]) == options.chapter]
            if not chapters:
                raise ConfigError(f"chapter {options.chapter} does not exist")
        terms = approved_terms(db, book_id)
        config = _lint_config(settings)
        chapter_index_by_id = {int(chapter["id"]): int(chapter["idx"]) for chapter in chapters}
        keep: set[str] = set()
        book_segments = [
            segment for chapter in chapters for segment in db.segments(chapter_id=int(chapter["id"]))
        ]
        translations: dict[str, Any] = {
            str(segment["id"]): row
            for segment in book_segments
            if (row := db.current_translation(str(segment["id"]))) is not None
        }
        lint_segments, lint_translations = _lint_objects(book_segments, translations)
        by_id = {item.id: item for item in lint_segments}
        chapter_windows: dict[int, list[Any]] = {}
        for chapter in chapters:
            chapter_segments = db.segments(chapter_id=int(chapter["id"]))
            windows = plan_windows(
                chapter_segments,
                chapter_id=int(chapter["id"]),
                chapter_idx=int(chapter["idx"]),
                max_paragraphs=settings.pipeline.window.max_paragraphs,
                max_source_tokens=settings.pipeline.window.max_source_tokens,
                overlap_paragraphs=settings.pipeline.window.overlap_paragraphs,
            )
            chapter_windows[int(chapter["id"])] = windows

        # Segment scope.
        for segment in book_segments:
            item = by_id[str(segment["id"])]
            found = check_segment(item, lint_translations.get(item.id), terms, config)
            for issue in found:
                keep.add(
                    _record(
                        db,
                        issue,
                        scope="segment",
                        ref=item.id,
                        chapter_id=int(segment["chapter_id"]),
                        segment_id=item.id,
                    )
                )
            if str(segment["kind"]) == "footnote" and item.id not in lint_translations:
                keep.add(
                    _record(
                        db,
                        LintIssue("L19", Severity.ERROR, f"footnote {item.id} is not translated"),
                        scope="segment",
                        ref=item.id,
                        chapter_id=int(segment["chapter_id"]),
                        segment_id=item.id,
                    )
                )

        # Window scope.
        for chapter in chapters:
            for window in chapter_windows[int(chapter["id"])]:
                ids = set(window.segment_ids)
                window_trs = {key: value for key, value in lint_translations.items() if key in ids}
                found = check_window(
                    LintWindow(
                        id=window.id,
                        segments=[item for item in lint_segments if item.id in ids],
                    ),
                    window_trs,
                    terms,
                )
                for issue in found:
                    keep.add(
                        _record(
                            db,
                            issue,
                            scope="window",
                            ref=window.id,
                            chapter_id=int(chapter["id"]),
                            segment_id=None,
                        )
                    )

        # Chapter scope (coverage, footnote integrity, style drift).
        baseline = _baseline_metrics(book_segments, lint_translations)
        for chapter in chapters:
            chapter_segments = db.segments(chapter_id=int(chapter["id"]))
            chapter_lint, _ = _lint_objects(chapter_segments, translations)
            found = check_chapter(
                LintChapter(
                    idx=int(chapter["idx"]),
                    segments=chapter_lint,
                    translations=lint_translations,
                    baseline=baseline,
                )
            )
            for issue in found:
                keep.add(
                    _record(
                        db,
                        issue,
                        scope="chapter",
                        ref=str(chapter["idx"]),
                        chapter_id=int(chapter["id"]),
                        segment_id=issue.context.get("segment_id") if issue.context else None,
                    )
                )

        # Book scope.
        for issue in check_book(
            LintBook(
                segments=lint_segments,
                translations=lint_translations,
                glossary=terms,
            )
        ):
            keep.add(
                _record(
                    db,
                    issue,
                    scope="book",
                    ref="book",
                    chapter_id=None,
                    segment_id=issue.context.get("segment_id") if issue.context else None,
                )
            )

        stale = 0
        if options.stale_glossary:
            digest = _glossary_digest(terms)
            current_terms = {term.en for term in terms}
            for segment in book_segments:
                row = translations.get(str(segment["id"]))
                if row is None or str(row["glossary_hash"] or "") == digest:
                    continue
                stale += 1
                try:
                    injected = set(json.loads(row["glossary_terms_json"] or "[]"))
                except json.JSONDecodeError:
                    injected = set()
                changed = sorted(current_terms.symmetric_difference(injected))[:8]
                if not changed:
                    changed = ["an approved Persian form changed"]
                keep.add(
                    _record(
                        db,
                        LintIssue(
                            "STALE_GLOSSARY",
                            Severity.WARN,
                            f"translation is stale relative to the approved glossary; changed: {changed}",
                            context={"segment_id": str(segment["id"]), "changed_terms": changed},
                        ),
                        scope="segment",
                        ref=str(segment["id"]),
                        chapter_id=int(segment["chapter_id"]),
                        segment_id=str(segment["id"]),
                    )
                )

        _resolve_stale(db, book_id, keep, [int(chapter["id"]) for chapter in chapters])

        # Risk scores and flags.
        window_counts = _window_issue_counts(
            db, [window for windows in chapter_windows.values() for window in windows]
        )
        result = QaResult(book_slug=str(book["slug"]), stale=stale)
        result.groups = dict.fromkeys(GROUPS, 0)
        flags_rows: list[dict[str, Any]] = []
        for segment in book_segments:
            segment_id = str(segment["id"])
            issues = db.issues_for(segment_id=segment_id, status="open")
            suggestion_count = int(
                db.scalar(
                    "SELECT COUNT(*) FROM glossary_suggestions WHERE segment_id = ? AND status = 'pending'",
                    (segment_id,),
                )
                or 0
            )
            risk = compute_risk(
                segment,
                issues,
                window_issue_count=window_counts.get(segment_id, 0),
                unapproved_suggestions=suggestion_count,
            )
            db.set_segment_risk(segment_id, risk)
            codes = sorted({str(issue["code"]) for issue in issues})
            for group, group_codes in GROUPS.items():
                if set(codes) & group_codes:
                    result.groups[group] += 1
            if (
                options.group
                and options.group != "all"
                and not (set(codes) & GROUPS.get(options.group, set()))
            ):
                continue
            result.issues += len(issues)
            result.errors += sum(1 for issue in issues if str(issue["severity"]) == "error")
            result.criticals += sum(1 for issue in issues if str(issue["severity"]) == "critical")
            translation = translations.get(segment_id)
            if risk >= settings.review.flag_threshold or issues:
                flags_rows.append(
                    _flag_row(
                        segment,
                        risk,
                        issues,
                        chapter_idx=chapter_index_by_id.get(
                            int(segment["chapter_id"]), int(segment["chapter_id"])
                        ),
                        fa_text=str(translation["fa_text"]) if translation is not None else "",
                    )
                )

        low_risk = [
            segment
            for segment in book_segments
            if segment["id"] not in {row["segment_id"] for row in flags_rows}
            and str(segment["status"]) != SegmentStatus.SKIPPED
        ]
        sample_size = int(len(low_risk) * settings.review.sample_fraction)
        if low_risk and sample_size:
            seeded = random.Random(sha256_text(str(book["slug"]))[:16])
            for segment in seeded.sample(low_risk, min(sample_size, len(low_risk))):
                translation = translations.get(str(segment["id"]))
                flags_rows.append(
                    _flag_row(
                        segment,
                        int(segment["risk_score"]),
                        [],
                        chapter_idx=chapter_index_by_id.get(
                            int(segment["chapter_id"]), int(segment["chapter_id"])
                        ),
                        fa_text=str(translation["fa_text"]) if translation is not None else "",
                        sample=True,
                    )
                )
        flags_rows.sort(key=lambda row: (-int(row["risk_score"]), str(row["segment_id"])))
        result.flags = len(flags_rows)
        result.flags_path = _write_flags(
            base / "review" / "flags.csv", flags_rows, preserve=options.preserve_flags
        )
        result.rows = flags_rows
        in_scope = [
            segment for segment in book_segments if str(segment["status"]) != SegmentStatus.SKIPPED
        ]
        covered = 0
        for segment in in_scope:
            row = translations.get(str(segment["id"]))
            if row is None:
                continue
            text = str(row["fa_text"]).strip()
            if text and re.search(r"[\u0600-\u06FF]", text):
                covered += 1
        result.coverage = covered / max(1, len(in_scope))
        return result
    finally:
        db.close()


def _baseline_metrics(
    segments: list[Any], translations: dict[str, LintTranslation]
) -> dict[str, float]:
    if not segments:
        return {}
    texts = [translations[str(segment["id"])].fa_text for segment in segments if str(segment["id"]) in translations]
    if not texts:
        return {}
    words = sum(count_words(text) for text in texts)
    return {
        "mean_sentence_words": words / max(1, len(texts)),
        "dialogue_ratio": sum(text.count("«") for text in texts) / max(1, len(texts)),
        "unique_word_ratio": len({word for text in texts for word in text.split()}) / max(1, words),
    }


def _flag_row(
    segment: Any,
    risk: int,
    issues: list[Any],
    *,
    chapter_idx: int,
    fa_text: str = "",
    sample: bool = False,
) -> dict[str, Any]:
    return {
        "segment_id": str(segment["id"]),
        "chapter": chapter_idx,
        "risk_score": int(risk),
        "codes": "|".join(sorted({str(issue["code"]) for issue in issues})),
        "severities": "|".join(sorted({str(issue["severity"]) for issue in issues})),
        "source_excerpt": str(segment["source_text"])[:160].replace("\n", " "),
        "fa_excerpt": fa_text[:160].replace("\n", " "),
        "sample_flag": "true" if sample else "false",
        "reviewed": "false",
        "reviewer_note": "",
    }


FLAGS_COLUMNS = [
    "segment_id",
    "chapter",
    "risk_score",
    "codes",
    "severities",
    "source_excerpt",
    "fa_excerpt",
    "sample_flag",
    "reviewed",
    "reviewer_note",
]


def build_flag_row(
    segment: Any,
    risk: int,
    issues: list[Any],
    *,
    chapter_idx: int,
    fa_text: str = "",
    sample: bool = False,
) -> dict[str, Any]:
    return _flag_row(
        segment,
        risk,
        issues,
        chapter_idx=chapter_idx,
        fa_text=fa_text,
        sample=sample,
    )


def _write_flags(
    path: Path, rows: list[dict[str, Any]], *, preserve: bool = False
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if preserve and path.is_file():
        with path.open("r", encoding="utf-8", newline="") as handle:
            existing = [dict(row) for row in csv.DictReader(handle)]
        by_id = {str(row.get("segment_id", "")): row for row in existing}
        merged: list[dict[str, Any]] = []
        for row in rows:
            previous = by_id.pop(str(row.get("segment_id", "")), None)
            if previous is not None and previous.get("reviewed") == "true":
                row["reviewed"] = "true"
                row["reviewer_note"] = previous.get("reviewer_note", "")
            merged.append(row)
        merged.extend(by_id.values())
        rows = merged
    temp = path.with_name(f"{path.name}.tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FLAGS_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(path)
    return path


def accept_issue(
    *, root: Path, slug: str | None, settings: Settings, issue_id: int, note: str, reviewer: str
) -> None:
    base = find_book_dir(root, slug, settings.project.books_dir)
    db = Database(base / "book.db")
    db.initialize()
    try:
        row = db.fetchone("SELECT * FROM issues WHERE id = ?", (issue_id,))
        if row is None:
            raise RuntimeFailure(
                f"issue {issue_id} does not exist", hint="run `tj qa` to refresh issue ids"
            )
        db.set_issue_status(int(issue_id), "waived", resolved_by=reviewer, resolution=note)
    finally:
        db.close()


def resolve_issue(*, root: Path, slug: str | None, settings: Settings, issue_id: int) -> None:
    base = find_book_dir(root, slug, settings.project.books_dir)
    db = Database(base / "book.db")
    db.initialize()
    try:
        row = db.fetchone("SELECT * FROM issues WHERE id = ?", (issue_id,))
        if row is None:
            raise RuntimeFailure(f"issue {issue_id} does not exist")
        db.set_issue_status(int(issue_id), "resolved")
    finally:
        db.close()
