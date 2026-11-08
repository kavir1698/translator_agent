"""`shz audit`: whole-book consistency audit, read-through, deterministic checks and repairs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from shahrzad.agents.analyst import (
    brief_card,
    brief_hash,
    default_brief,
    load_approved_brief,
)
from shahrzad.agents.consistency_auditor import ConsistencyAuditor
from shahrzad.agents.readthrough import ReadThroughEditor
from shahrzad.agents.reviser import Reviser
from shahrzad.context.glossary_match import glossary_block
from shahrzad.context.packer import PackState, build_pack, render_draft
from shahrzad.core.config import Settings, redacted_config
from shahrzad.core.cost import ModelProfile, is_peak
from shahrzad.core.db import Database
from shahrzad.core.errors import (
    BudgetPaused,
    ConfigError,
    MissingFixture,
    SchemaValidationError,
    TransientProviderError,
)
from shahrzad.core.ids import sha256_text, utc_now_iso
from shahrzad.core.models import (
    ChapterStatus,
    PassKind,
    RunStatus,
    Severity,
    normalize_severity,
)
from shahrzad.core.paths import find_book_dir
from shahrzad.llm.budget import BudgetGuard
from shahrzad.llm.cache import ResponseCache
from shahrzad.llm.promptlib import load as load_prompt
from shahrzad.llm.provider import Provider
from shahrzad.persian.lint import (
    GlossaryTerm,
    LintConfig,
    LintSegment,
    LintTranslation,
    check_segment,
    term_in_text,
)
from shahrzad.persian.markup import normalize_translation
from shahrzad.persian.normalize import normalize_fa

from .glossary_ops import approved_terms
from .window import plan_windows

_PERSIAN_WORD = re.compile(r"[\u0600-\u06FF\u200c]{2,}")
_SAMPLE_PAIR_LIMIT = 1500
_AMBIGUOUS_RATIO = 94.0


@dataclass
class AuditOptions:
    chapter: int | None = None
    sample: int = 3
    readthrough: bool | None = None
    max_usd: float | None = None


@dataclass
class AuditResult:
    run_id: str = ""
    status: str = "completed"
    paused: bool = False
    chapters: int = 0
    a10_findings: int = 0
    a11_issues: int = 0
    deterministic_issues: int = 0
    repairs: int = 0
    cost_usd: float = 0.0
    coverage: float = 0.0
    adherence: float = 0.0
    calls: int = 0
    cached_calls: int = 0
    report_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _AuditRun:
    base: Path
    db: Database
    book: Any
    settings: Settings
    profile: ModelProfile
    provider: Provider
    options: AuditOptions
    cache: ResponseCache
    run_id: str
    book_id: int
    brief: Any
    terms: list[GlossaryTerm]
    guard: BudgetGuard
    started_at: str
    peak: bool
    readthrough: bool
    chapters: list[Any] = field(default_factory=list)
    book_segment_ids: set[str] = field(default_factory=set)
    stats: dict[str, Any] = field(default_factory=dict)
    paused: bool = False
    warnings: list[str] = field(default_factory=list)

    def tier(self, agent: str) -> Any:
        if agent in {"A8", "A11"}:
            return self.profile.tier("quality")
        return self.profile.tier("balanced")


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


def _glossary_digest(terms: list[GlossaryTerm]) -> str:
    payload = json.dumps(
        [[term.en, term.fa, term.kind, list(term.aliases_en), list(term.aliases_fa)] for term in terms],
        sort_keys=True,
        ensure_ascii=False,
    )
    return f"sha256:{sha256_text(payload)}"


def _canonical(text: str) -> str:
    return re.sub(r"[\s\u200c]+", "", normalize_fa(text))


def _norm_token(text: str) -> str:
    return _canonical(text)


def _char_count(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False)) if not isinstance(value, str) else len(value)


def _check_budget(run: _AuditRun, *, agent: str, input_chars: int) -> None:
    tier = run.tier(agent)
    run.guard.spent_usd = run.db.spend_for_run(run.run_id)
    run.guard.check(
        max_input_tokens=max(1_000, input_chars // 3 + 2_000),
        max_output_tokens=tier.max_output_tokens,
        pricing=tier.pricing,
        hint=(
            "the audit is resumable; raise budget.max_usd or pass --max-usd, "
            "then run `shz audit` again"
        ),
    )


def _evenly_spaced(items: list[Any], count: int) -> list[Any]:
    if count <= 0:
        return []
    if len(items) <= count:
        return list(items)
    if count == 1:
        return [items[len(items) // 2]]
    indexes = sorted({round(index * (len(items) - 1) / (count - 1)) for index in range(count)})
    return [items[index] for index in indexes]


def _pair_block(segment: Any, translation: Any) -> str:
    pair = (
        f"[{segment['id']}]\n"
        f"EN: {str(segment['source_text']).strip()}\n"
        f"FA: {str(translation['fa_text']).strip()}"
    )
    if len(pair) <= _SAMPLE_PAIR_LIMIT:
        return pair
    return pair[: _SAMPLE_PAIR_LIMIT - 1].rstrip() + "…"


def _sample_pairs(run: _AuditRun, chapter: Any, segments: list[Any]) -> str:
    window_settings = run.settings.pipeline.window
    windows = plan_windows(
        segments,
        chapter_id=int(chapter["id"]),
        chapter_idx=int(chapter["idx"]),
        max_paragraphs=window_settings.max_paragraphs,
        max_source_tokens=window_settings.max_source_tokens,
        overlap_paragraphs=window_settings.overlap_paragraphs,
    )
    pairs: list[str] = []
    for window in _evenly_spaced(windows, run.options.sample):
        for segment in window.segments:
            translation = run.db.current_translation(str(segment["id"]))
            if translation is None:
                continue
            pairs.append(_pair_block(segment, translation))
    return "\n\n".join(pairs)


def _chapter_summary(run: _AuditRun, chapter: Any) -> str:
    row = run.db.get_chapter_summary(int(chapter["id"]))
    return str(row["summary_en"]) if row is not None else ""


def _ledger_text(run: _AuditRun) -> str:
    lines: list[str] = []
    for row in run.db.ledger_entries(run.book_id)[-30:]:
        try:
            value = json.loads(str(row["value_json"] or "{}"))
        except json.JSONDecodeError:
            value = str(row["value_json"] or "")
        rendered = (
            json.dumps(value, sort_keys=True, ensure_ascii=False)
            if not isinstance(value, str)
            else value
        )
        lines.append(f"- {row['kind']}: {row['key']} = {rendered}")
    return "\n".join(lines)


def _style_state(run: _AuditRun, chapter: Any) -> str:
    parts = ["BRIEF:", brief_card(run.brief)]
    decisions = [
        row
        for row in run.db.ledger_entries(run.book_id)
        if str(row["kind"]) == "decision"
    ]
    parts.extend(["", "DECISIONS:"])
    if decisions:
        for row in decisions:
            try:
                value = json.loads(str(row["value_json"] or "{}"))
            except json.JSONDecodeError:
                value = str(row["value_json"] or "")
            text = str(value.get("value", value)) if isinstance(value, dict) else str(value)
            parts.append(f"- {row['key']}: {text}")
    else:
        parts.append("(none)")
    preceding = [
        row for row in run.db.chapter_summaries(run.book_id) if int(row["idx"]) < int(chapter["idx"])
    ][-2:]
    for row in preceding:
        parts.extend(["", f"TONE (chapter {row['idx']}): {str(row['tone_notes'] or '(none)')}"])
    return "\n".join(parts)


def _persist_a10(run: _AuditRun, chapter: Any, findings: Any, book_segment_ids: set[str]) -> int:
    chapter_id = int(chapter["id"])
    chapter_idx = int(chapter["idx"])
    count = 0
    for finding in findings.findings:
        severity = normalize_severity(finding.severity)
        code = f"A10_{finding.kind.upper()}"
        digest = sha256_text(f"{finding.evidence}|{finding.explanation}")[:12]
        segment_id = finding.segment_id if finding.segment_id in book_segment_ids else None
        scope = f"seg:{segment_id}" if segment_id else f"ch:{chapter_idx}"
        run.db.upsert_issue(
            stage="audit",
            code=code,
            dedupe_key=f"{scope}:{code}:{digest}",
            severity=severity,
            message=finding.explanation or finding.evidence,
            segment_id=segment_id,
            chapter_id=chapter_id,
            quote_target=finding.evidence,
            context={
                "agent": "A10",
                "agent_severity": finding.severity,
                "conflicts_with": finding.conflicts_with,
                "explanation": finding.explanation,
                "suggested_resolution": finding.suggested_resolution,
            },
        )
        count += 1
    return count


def _persist_ledger_corrections(run: _AuditRun, chapter: Any, corrections: list[dict[str, str]]) -> None:
    chapter_idx = int(chapter["idx"])
    for correction in corrections:
        digest = sha256_text(json.dumps(correction, sort_keys=True, ensure_ascii=False))[:12]
        run.db.upsert_issue(
            stage="audit",
            code="LEDGER_CORRECTION",
            dedupe_key=f"ch:{chapter_idx}:LEDGER_CORRECTION:{digest}",
            severity=Severity.WARN,
            message=f"ledger correction suggested: {correction}",
            chapter_id=int(chapter["id"]),
            context={"agent": "A10", "correction": correction},
        )


def _locate_passage(passage: str, translations: list[tuple[str, str]]) -> str | None:
    needle = _canonical(passage)
    if not needle:
        return None
    for segment_id, text in translations:
        if needle in _canonical(text):
            return segment_id
    return None


def _persist_a11(run: _AuditRun, chapter: Any, findings: Any) -> int:
    chapter_id = int(chapter["id"])
    chapter_idx = int(chapter["idx"])
    segments = run.db.segments(chapter_id=chapter_id)
    translations: list[tuple[str, str]] = []
    for segment in segments:
        row = run.db.current_translation(str(segment["id"]))
        if row is not None:
            translations.append((str(segment["id"]), str(row["fa_text"])))
    count = 0
    for issue in findings.issues[:5]:
        severity = normalize_severity(issue.severity)
        code = f"A11_{issue.problem.upper()}"
        digest = sha256_text(f"{issue.passage}|{issue.why_it_hurts}")[:12]
        segment_id = _locate_passage(issue.passage, translations)
        scope = f"seg:{segment_id}" if segment_id else f"ch:{chapter_idx}"
        run.db.upsert_issue(
            stage="readthrough",
            code=code,
            dedupe_key=f"{scope}:{code}:{digest}",
            severity=severity,
            message=issue.why_it_hurts,
            segment_id=segment_id,
            chapter_id=chapter_id,
            quote_target=issue.passage,
            context={
                "agent": "A11",
                "agent_severity": issue.severity,
                "suggested_rewrite": issue.suggested_rewrite,
                "why_it_hurts": issue.why_it_hurts,
                "voice_verdict": findings.voice_verdict,
            },
        )
        count += 1
    return count


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    shorter, longer = sorted((len(left), len(right)))
    if shorter / longer < 0.75:
        return 0.0
    return float(fuzz.ratio(left, right))


def _contains_en(name: str, source: str) -> bool:
    if not name.strip():
        return False
    return bool(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", source, re.IGNORECASE))


def _approved_name_forms(db: Database, book_id: int, terms: list[GlossaryTerm]) -> dict[str, dict[str, str]]:
    forms: dict[str, dict[str, str]] = {}
    kinds = {"character", "place", "organization", "title"}
    for term in terms:
        if term.kind not in kinds:
            continue
        for form in (term.fa, *term.aliases_fa):
            key = _norm_token(form)
            if key:
                forms.setdefault(key, {"form": form, "source": term.en})
    for row in db.ledger_entries(book_id):
        if str(row["kind"]) != "character":
            continue
        try:
            value = json.loads(str(row["value_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        form = str(value.get("fa", "") or "")
        key = _norm_token(form)
        if key:
            forms.setdefault(key, {"form": form, "source": str(value.get("en") or row["key"])})
    return forms


def _chapter_texts(db: Database, book_id: int) -> list[tuple[int, int, str]]:
    texts: list[tuple[int, int, str]] = []
    for chapter in db.chapters(book_id):
        joined: list[str] = []
        for segment in db.segments(chapter_id=int(chapter["id"])):
            row = db.current_translation(str(segment["id"]))
            if row is not None:
                joined.append(str(row["fa_text"]))
        texts.append(
            (int(chapter["id"]), int(chapter["idx"]), _canonical(" ".join(joined)))
        )
    return texts


def _form_chapters(
    forms: dict[str, dict[str, str]], chapter_texts: list[tuple[int, int, str]]
) -> dict[str, set[int]]:
    result: dict[str, set[int]] = {}
    for key in forms:
        if not key:
            continue
        result[key] = {
            chapter_idx for _, chapter_idx, text in chapter_texts if key in text
        }
    return result


def _deterministic_audit(run: _AuditRun) -> int:
    terms = run.terms
    glossary_forms: dict[str, list[tuple[str, GlossaryTerm]]] = {}
    for term in terms:
        for form in (term.fa, *term.aliases_fa):
            key = _norm_token(form)
            if key:
                glossary_forms.setdefault(key, []).append((form, term))
    name_forms = _approved_name_forms(run.db, run.book_id, terms)
    chapter_texts = _chapter_texts(run.db, run.book_id)
    name_form_chapters = _form_chapters(name_forms, chapter_texts)
    count = 0
    for chapter in run.chapters:
        chapter_id = int(chapter["id"])
        chapter_idx = int(chapter["idx"])
        for segment in run.db.segments(chapter_id=chapter_id):
            row = run.db.current_translation(str(segment["id"]))
            if row is None:
                continue
            segment_id = str(segment["id"])
            source_text = str(segment["source_text"])
            fa_text = str(row["fa_text"])
            for term in terms:
                names = (term.en, *term.aliases_en)
                if not any(_contains_en(name, source_text) for name in names):
                    continue
                forms = (term.fa, *term.aliases_fa)
                if any(term_in_text(form, fa_text) for form in forms):
                    continue
                # Persian routinely drops a repeated subject, so a missing *name*
                # is evidence for review, not a safe automatic rewrite.
                name_kind = term.kind in {"character", "place"}
                digest = sha256_text(term.en)[:12]
                run.db.upsert_issue(
                    stage="audit",
                    code="GLOSSARY_MISSING",
                    dedupe_key=f"seg:{segment_id}:GLOSSARY_MISSING:{digest}",
                    severity=Severity.WARN if name_kind else Severity.ERROR,
                    message=f"approved term {term.en!r} is missing from the translation",
                    segment_id=segment_id,
                    chapter_id=chapter_id,
                    quote_target=term.fa,
                    context={
                        "agent": "code",
                        "term_en": term.en,
                        "term_fa": term.fa,
                        "kind": term.kind,
                        "needs_adjudication": name_kind,
                    },
                )
                count += 1
            tokens: dict[str, str] = {}
            for token in _PERSIAN_WORD.findall(fa_text):
                key = _norm_token(token)
                if key:
                    tokens.setdefault(key, token)
            for token_key, token in tokens.items():
                matched_glossary = False
                if token_key not in glossary_forms:
                    best_glossary: tuple[float, str, GlossaryTerm] | None = None
                    for form_key, entries in glossary_forms.items():
                        ratio = _similarity(token_key, form_key)
                        if ratio < 88:
                            continue
                        if best_glossary is None or ratio > best_glossary[0]:
                            form, term = entries[0]
                            best_glossary = (ratio, form, term)
                    if best_glossary is not None:
                        ratio, form, term = best_glossary
                        ambiguous = ratio < _AMBIGUOUS_RATIO
                        approved_chapters = name_form_chapters.get(_norm_token(form), set())
                        cross_chapter = (
                            bool(approved_chapters) and chapter_idx not in approved_chapters
                        )
                        digest = sha256_text(f"{term.en}|{token}")[:12]
                        run.db.upsert_issue(
                            stage="audit",
                            code="GLOSSARY_VARIANT",
                            dedupe_key=f"seg:{segment_id}:GLOSSARY_VARIANT:{digest}",
                            severity=(
                                Severity.ERROR
                                if cross_chapter or not ambiguous
                                else Severity.WARN
                            ),
                            message=(
                                f"unapproved variant of {term.en!r}: {token} "
                                f"(approved: {form}, ratio {ratio:.1f})"
                            ),
                            segment_id=segment_id,
                            chapter_id=chapter_id,
                            quote_target=token,
                            context={
                                "agent": "code",
                                "approved_form": form,
                                "observed": token,
                                "ratio": round(ratio, 1),
                                "term_en": term.en,
                                "ambiguous": ambiguous,
                                "needs_adjudication": ambiguous,
                            },
                        )
                        matched_glossary = True
                        count += 1
                if token_key in name_forms or matched_glossary:
                    continue
                best_name: tuple[float, str, dict[str, str]] | None = None
                for form_key, meta in name_forms.items():
                    ratio = _similarity(token_key, form_key)
                    if ratio < 88:
                        continue
                    if best_name is None or ratio > best_name[0]:
                        best_name = (ratio, form_key, meta)
                if best_name is None:
                    continue
                ratio, form_key, meta = best_name
                approved_chapters = name_form_chapters.get(form_key, set())
                severity = (
                    Severity.ERROR
                    if approved_chapters and chapter_idx not in approved_chapters
                    else Severity.WARN
                )
                ambiguous = ratio < _AMBIGUOUS_RATIO
                digest = sha256_text(f"{meta['form']}|{token}")[:12]
                run.db.upsert_issue(
                    stage="audit",
                    code="NAME_VARIANT",
                    dedupe_key=f"seg:{segment_id}:NAME_VARIANT:{digest}",
                    severity=severity,
                    message=(
                        f"possible name variant of {meta['source']!r}: {token} "
                        f"(approved: {meta['form']}, ratio {ratio:.1f})"
                    ),
                    segment_id=segment_id,
                    chapter_id=chapter_id,
                    quote_target=token,
                    context={
                        "agent": "code",
                        "approved_form": meta["form"],
                        "observed": token,
                        "ratio": round(ratio, 1),
                        "ambiguous": ambiguous,
                        "needs_adjudication": ambiguous,
                    },
                )
                count += 1
    return count


def _audit_chapter(run: _AuditRun, chapter: Any) -> None:
    chapter_id = int(chapter["id"])
    chapter_idx = int(chapter["idx"])
    segments = run.db.segments(chapter_id=chapter_id)
    auditor = ConsistencyAuditor()
    pairs = _sample_pairs(run, chapter, segments)
    summary_text = _chapter_summary(run, chapter)
    ledger_text = _ledger_text(run)
    glossary_text = glossary_block(run.terms)
    if pairs:
        _check_budget(
            run,
            agent="A10",
            input_chars=(
                len(pairs) + len(summary_text) + len(ledger_text) + len(glossary_text)
            ),
        )
        try:
            outcome = auditor.audit(
                provider=run.provider,
                tier=run.tier("A10"),
                db=run.db,
                run_id=run.run_id,
                cache=run.cache,
                chapter_index=chapter_idx,
                chapter_summary=summary_text,
                ledger=ledger_text,
                glossary=glossary_text,
                sample_pairs=pairs,
            )
        except (MissingFixture, SchemaValidationError, TransientProviderError) as exc:
            run.db.upsert_issue(
                stage="audit",
                code="A10_SKIPPED",
                dedupe_key=f"ch:{chapter_idx}:A10_SKIPPED",
                severity=Severity.WARN,
                message=f"consistency audit skipped for chapter {chapter_idx}: {exc}",
                chapter_id=chapter_id,
                context={"agent": "A10"},
            )
        else:
            run.stats["a10_findings"] = int(run.stats.get("a10_findings", 0)) + _persist_a10(
                run, chapter, outcome.value, run.book_segment_ids
            )
            _persist_ledger_corrections(run, chapter, outcome.value.ledger_corrections)
            verdicts = run.stats.setdefault("verdicts", {})
            verdicts[str(chapter_idx)] = outcome.value.verdict

    if run.readthrough:
        fa_texts = [
            str(row["fa_text"])
            for segment in segments
            if (row := run.db.current_translation(str(segment["id"]))) is not None
        ]
        chapter_fa_text = "\n\n".join(fa_texts)
        if chapter_fa_text.strip():
            style_state = _style_state(run, chapter)
            _check_budget(
                run,
                agent="A11",
                input_chars=len(chapter_fa_text) + len(style_state) + 1_000,
            )
            try:
                read_outcome = ReadThroughEditor().read(
                    provider=run.provider,
                    tier=run.tier("A11"),
                    db=run.db,
                    run_id=run.run_id,
                    cache=run.cache,
                    brief_card=brief_card(run.brief),
                    style_state=style_state,
                    chapter_fa_text=chapter_fa_text,
                )
            except (MissingFixture, SchemaValidationError, TransientProviderError) as exc:
                run.db.upsert_issue(
                    stage="audit",
                    code="A11_SKIPPED",
                    dedupe_key=f"ch:{chapter_idx}:A11_SKIPPED",
                    severity=Severity.WARN,
                    message=f"read-through skipped for chapter {chapter_idx}: {exc}",
                    chapter_id=chapter_id,
                    context={"agent": "A11"},
                )
            else:
                run.stats["a11_issues"] = int(run.stats.get("a11_issues", 0)) + _persist_a11(
                    run, chapter, read_outcome.value
                )

    fresh = run.db.get_chapter(chapter_id)
    if fresh is not None and fresh["status"] == ChapterStatus.TRANSLATED:
        run.db.transition_chapter(chapter_id, ChapterStatus.AUDITED)
    run.stats["chapters_audited"] = int(run.stats.get("chapters_audited", 0)) + 1


def _repair_tasks(run: _AuditRun) -> list[Any]:
    chapters = run.chapters
    if not chapters:
        return []
    marks = ",".join("?" for _ in chapters)
    ids = tuple(int(chapter["id"]) for chapter in chapters)
    return run.db.fetchall(
        f"""
        SELECT i.* FROM issues i
        JOIN segments s ON s.id = i.segment_id
        JOIN chapters c ON c.id = s.chapter_id
        WHERE i.stage = 'audit' AND i.status = 'open'
          AND i.severity IN ('error','critical')
          AND i.segment_id IS NOT NULL
          AND c.id IN ({marks})
        ORDER BY c.idx, s.idx, i.id
        """,
        ids,
    )


def _state(run: _AuditRun, chapter: Any) -> PackState:
    ledger_rows = run.db.ledger_entries(run.book_id)
    cast = tuple(row for row in ledger_rows if str(row["kind"]) == "character")
    return PackState(
        brief=run.brief,
        glossary_terms=tuple(run.terms),
        chapter_summary=_chapter_summary(run, chapter),
        ledger=tuple(ledger_rows),
        cast=cast,
    )


def _lint_error_count(
    run: _AuditRun,
    segment: Any,
    fa_text: str,
    fa_markup: str,
) -> int:
    lint_segment = LintSegment(
        id=str(segment["id"]),
        kind=str(segment["kind"]),
        source_text=str(segment["source_text"]),
        source_html=str(segment["source_html"]),
        nodes=_nodes(segment),
        status=str(segment["status"]),
    )
    issues = check_segment(
        lint_segment,
        LintTranslation(fa_text=fa_text, fa_markup=fa_markup),
        run.terms,
        _lint_config(run.settings),
    )
    return sum(
        1 for issue in issues if str(issue.severity) in {Severity.ERROR, Severity.CRITICAL}
    )


def _issue_context(row: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(row["context_json"] or "{}"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _run_repairs(run: _AuditRun) -> None:
    tasks = _repair_tasks(run)
    if not tasks:
        return
    grouped: dict[str, list[Any]] = {}
    for row in tasks:
        grouped.setdefault(str(row["segment_id"]), []).append(row)
    repairs: list[dict[str, Any]] = []
    for segment_id, rows in grouped.items():
        segment = run.db.get_segment(segment_id)
        if segment is None:
            continue
        current = run.db.current_translation(segment_id)
        if current is None:
            continue
        if str(current["pass"]) == PassKind.HUMAN:
            continue
        chapter = run.db.get_chapter(int(segment["chapter_id"]))
        if chapter is None:
            continue
        chapter_segments = run.db.segments(chapter_id=int(segment["chapter_id"]))
        index = next(
            (position for position, item in enumerate(chapter_segments) if str(item["id"]) == segment_id),
            None,
        )
        if index is None:
            continue
        previous = chapter_segments[max(0, index - 2) : index]
        previous_translations = {
            str(item["id"]): row
            for item in previous
            if (row := run.db.current_translation(str(item["id"]))) is not None
        }
        issues: list[dict[str, Any]] = []
        for row in rows:
            context = _issue_context(row)
            issues.append(
                {
                    "segment_id": segment_id,
                    "type": str(row["code"]),
                    "severity": context.get(
                        "agent_severity",
                        "critical" if str(row["severity"]) == Severity.CRITICAL else "major",
                    ),
                    "quote_target": str(row["quote_target"] or context.get("evidence", "")),
                    "problem": str(row["message"]),
                    "suggested_fix": str(context.get("suggested_resolution", "")),
                }
            )
        variables = build_pack(
            window_segments=[*previous, segment],
            todo_segments=[segment],
            state=_state(run, chapter),
            prev_segments=previous,
            prev_translations=previous_translations,
        )
        draft = render_draft([segment], {segment_id: current})
        _check_budget(
            run,
            agent="A8",
            input_chars=sum(_char_count(value) for value in variables.values()) + len(draft),
        )
        try:
            outcome = Reviser().revise(
                provider=run.provider,
                tier=run.tier("A8"),
                db=run.db,
                run_id=run.run_id,
                cache=run.cache,
                variables=variables,
                segments=[segment],
                draft=draft,
                issues=issues,
                repair_only=True,
            )
        except (MissingFixture, SchemaValidationError, TransientProviderError) as exc:
            run.db.upsert_issue(
                stage="audit",
                code="AUDIT_REPAIR_SKIPPED",
                dedupe_key=f"seg:{segment_id}:AUDIT_REPAIR_SKIPPED:{sha256_text(str(exc))[:12]}",
                severity=Severity.WARN,
                message=f"audit repair skipped for {segment_id}: {exc}",
                segment_id=segment_id,
                chapter_id=int(segment["chapter_id"]),
            )
            continue
        item = next(
            (result for result in outcome.value.segments if str(result.id) == segment_id),
            None,
        )
        if item is None:
            continue
        normalized = normalize_translation(
            item.fa_markup,
            _nodes(segment),
            digits=run.settings.persian.digits,
            keep_diacritics=run.settings.persian.keep_diacritics,
            ellipsis=run.settings.persian.ellipsis,
        )
        if normalized.problems or not normalized.fa_text.strip():
            continue
        if normalized.fa_markup == str(current["fa_markup"]):
            continue
        old_errors = _lint_error_count(
            run, segment, str(current["fa_text"]), str(current["fa_markup"])
        )
        new_errors = _lint_error_count(run, segment, normalized.fa_text, normalized.fa_markup)
        if new_errors > old_errors:
            continue
        reason = next(
            (
                str(_issue_context(row).get("suggested_resolution", ""))
                for row in rows
                if _issue_context(row).get("suggested_resolution")
            ),
            str(rows[0]["message"]),
        )
        codes = "|".join(sorted({str(row["code"]) for row in rows}))
        old_version = int(current["version"])
        new_version = run.db.add_translation(
            segment_id=segment_id,
            pass_kind=PassKind.REPAIR,
            fa_text=normalized.fa_text,
            fa_markup=normalized.fa_markup,
            model=outcome.model,
            notes={"audit_repair": True, "reason": reason},
            glossary_hash=_glossary_digest(run.terms),
            glossary_terms=[term.en for term in run.terms],
            prompt_hash=outcome.prompt_hash,
            tokens_in=outcome.tokens_in,
            tokens_out=outcome.tokens_out,
            cost_usd=outcome.cost_usd,
            latency_ms=outcome.latency_ms,
        )
        for row in rows:
            run.db.set_issue_status(int(row["id"]), "resolved")
        repairs.append(
            {
                "segment_id": segment_id,
                "code": codes,
                "old_version": old_version,
                "new_version": new_version,
                "model": outcome.model,
                "reason": reason,
            }
        )
    run.stats["repairs"] = repairs
    run.stats["repair_count"] = len(repairs)


def _serialize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _artifact_hashes(base: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("brief.yaml", "glossary.yaml", "anchors.yaml", "ledger.jsonl"):
        path = base / "artifacts" / name
        if path.is_file():
            result[name.split(".")[0]] = f"sha256:{sha256_text(path.read_text(encoding='utf-8'))}"
    return result


def _write_calls(run_dir: Path, calls: list[Any]) -> None:
    with (run_dir / "calls.jsonl").open("w", encoding="utf-8") as handle:
        for row in calls:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def _coverage(run: _AuditRun) -> float:
    total = 0
    covered = 0
    for chapter in run.chapters:
        for segment in run.db.segments(chapter_id=int(chapter["id"])):
            if str(segment["status"]) == "skipped":
                continue
            total += 1
            row = run.db.current_translation(str(segment["id"]))
            if row is None:
                continue
            text = str(row["fa_text"]).strip()
            if text and re.search(r"[\u0600-\u06FF]", text):
                covered += 1
    return covered / max(1, total)


def _adherence(run: _AuditRun) -> float:
    if not run.chapters:
        return 0.0
    clean = 0
    for chapter in run.chapters:
        count = int(
            run.db.scalar(
                """
                SELECT COUNT(*) FROM issues i
                LEFT JOIN segments s ON s.id = i.segment_id
                WHERE i.status = 'open'
                  AND i.stage IN ('audit','readthrough')
                  AND i.severity IN ('error','critical')
                  AND COALESCE(i.chapter_id, s.chapter_id) = ?
                """,
                (int(chapter["id"]),),
            )
            or 0
        )
        if count == 0:
            clean += 1
    return clean / len(run.chapters)


def _issue_counts(run: _AuditRun) -> list[tuple[str, str, int]]:
    if not run.chapters:
        return []
    marks = ",".join("?" for _ in run.chapters)
    ids = tuple(int(chapter["id"]) for chapter in run.chapters)
    rows = run.db.fetchall(
        f"""
        SELECT i.code, i.severity, COUNT(*) AS n FROM issues i
        LEFT JOIN segments s ON s.id = i.segment_id
        WHERE i.status = 'open' AND i.stage IN ('audit','readthrough')
          AND COALESCE(i.chapter_id, s.chapter_id) IN ({marks})
        GROUP BY i.code, i.severity
        ORDER BY i.code, i.severity
        """,
        ids,
    )
    return [(str(row["code"]), str(row["severity"]), int(row["n"])) for row in rows]


def _l15_chapters(run: _AuditRun) -> list[tuple[int, str]]:
    if not run.chapters:
        return []
    marks = ",".join("?" for _ in run.chapters)
    ids = tuple(int(chapter["id"]) for chapter in run.chapters)
    rows = run.db.fetchall(
        f"""
        SELECT c.idx, i.message FROM issues i
        JOIN chapters c ON c.id = COALESCE(i.chapter_id, (
            SELECT s.chapter_id FROM segments s WHERE s.id = i.segment_id
        ))
        WHERE i.stage = 'qa' AND i.code = 'L15' AND i.status = 'open'
          AND c.id IN ({marks})
        ORDER BY c.idx, i.id
        """,
        ids,
    )
    return [(int(row["idx"]), str(row["message"])) for row in rows]


def _report(run: _AuditRun, stats: dict[str, Any], calls: list[Any]) -> str:
    by_agent: dict[str, dict[str, float]] = {}
    for row in calls:
        entry = by_agent.setdefault(str(row["agent"]), {"calls": 0, "in": 0, "out": 0, "cost": 0.0})
        entry["calls"] += 1
        entry["in"] += int(row["tokens_in"] or 0)
        entry["out"] += int(row["tokens_out"] or 0)
        entry["cost"] += float(row["cost_usd"] or 0.0)
    words = sum(int(chapter["word_count"] or 0) for chapter in run.chapters)
    lines = [
        f"# Audit run {run.run_id}",
        "",
        f"- book: {run.book['slug']}",
        f"- status: {stats.get('status', 'completed')}",
        f"- started: {run.started_at}",
        f"- finished: {stats.get('finished_at', '')}",
        f"- tariff: {stats.get('tariff')}",
        f"- chapters audited: {stats.get('chapters_audited', 0)}",
        f"- coverage: {stats.get('coverage', 0):.1%}",
        f"- adherence: {stats.get('adherence', 0):.1%} (chapters with no open audit/readthrough error or critical)",
        f"- A10 findings: {stats.get('a10_findings', 0)}",
        f"- A11 issues: {stats.get('a11_issues', 0)}",
        f"- deterministic findings: {stats.get('deterministic_issues', 0)}",
        f"- repairs: {stats.get('repair_count', 0)}",
        f"- cache hit rate: {stats.get('cache_hit_rate', 0):.1%}",
        f"- cost: ${stats.get('cost_usd', 0):.4f}",
        f"- cost per 1,000 words: ${(stats.get('cost_usd', 0.0) / max(1, words) * 1000):.4f}",
        f"- elapsed: {stats.get('elapsed_s', 0):.1f}s",
        "",
        "## Cost by agent",
        "",
        "| agent | calls | tokens in | tokens out | USD |",
        "|---|---:|---:|---:|---:|",
    ]
    for agent, entry in sorted(by_agent.items()):
        lines.append(
            f"| {agent} | {int(entry['calls'])} | {int(entry['in'])} | {int(entry['out'])} | "
            f"${entry['cost']:.4f} |"
        )
    if not by_agent:
        lines.append("| (none) | 0 | 0 | 0 | $0.0000 |")
    lines.extend(["", "## Open issues by code and severity", "", "| code | severity | count |", "|---|---|---:|"])
    counts = _issue_counts(run)
    for code, severity, number in counts:
        lines.append(f"| {code} | {severity} | {number} |")
    if not counts:
        lines.append("| (none) | | 0 |")
    lines.extend(
        [
            "",
            "## Repairs",
            "",
            "| segment | task code | old version | new version | model | reason |",
            "|---|---|---:|---:|---|---|",
        ]
    )
    repairs = stats.get("repairs") or []
    for repair in repairs:
        reason = str(repair.get("reason", "")).replace("|", "\\|")
        lines.append(
            f"| {repair.get('segment_id')} | {repair.get('code')} | {repair.get('old_version')} | "
            f"{repair.get('new_version')} | {repair.get('model')} | {reason} |"
        )
    if not repairs:
        lines.append("| (none) | | | | | |")
    lines.extend(["", "## Riskiest segments", "", "| segment | risk | status |", "|---|---:|---|"])
    marks = ",".join("?" for _ in run.chapters)
    ids = tuple(int(chapter["id"]) for chapter in run.chapters)
    rows = (
        run.db.fetchall(
            f"""
            SELECT s.id, s.risk_score, s.status FROM segments s
            WHERE s.chapter_id IN ({marks}) AND s.risk_score > 0
            ORDER BY s.risk_score DESC, s.id LIMIT 20
            """,
            ids,
        )
        if marks
        else []
    )
    for row in rows:
        lines.append(f"| {row['id']} | {row['risk_score']} | {row['status']} |")
    if not rows:
        lines.append("| (none) | 0 | |")
    lines.extend(["", "## Style drift (L15)", ""])
    drift = _l15_chapters(run)
    if drift:
        for idx, message in drift:
            lines.append(f"- chapter {idx}: {message}")
    else:
        lines.append("(none)")
    if stats.get("budget_paused"):
        lines.extend(
            [
                "",
                "## Budget pause",
                "",
                "The audit paused at the budget cap. It is resumable: run `shz audit` again "
                "after raising `budget.max_usd` or pass `--max-usd`.",
            ]
        )
    return "\n".join(lines) + "\n"


def _finalize(run: _AuditRun, result: AuditResult, *, status: str) -> AuditResult:
    db = run.db
    calls = db.calls_for_run(run.run_id)
    cost = round(sum(float(row["cost_usd"] or 0.0) for row in calls), 8)
    cached = sum(1 for row in calls if int(row["cached"]))
    finished_at = utc_now_iso()
    try:
        elapsed = (
            datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
            - datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
        ).total_seconds()
    except ValueError:
        elapsed = 0.0
    coverage = _coverage(run)
    adherence = _adherence(run)
    stats = {
        **run.stats,
        "status": status,
        "chapters": len(run.chapters),
        "chapters_audited": int(run.stats.get("chapters_audited", 0)),
        "calls": len(calls),
        "cached_calls": cached,
        "cache_hit_rate": round(cached / len(calls), 4) if calls else 1.0,
        "cost_usd": cost,
        "coverage": round(coverage, 4),
        "adherence": round(adherence, 4),
        "tariff": "peak" if run.peak else "offpeak",
        "elapsed_s": round(elapsed, 3),
        "finished_at": finished_at,
    }
    stats = _serialize(stats)
    db.update_run(run.run_id, stats=stats, cost_usd=cost)
    run_dir = run.base / "runs" / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_calls(run_dir, calls)
    prompt_versions = {
        prompt_id: load_prompt(prompt_id).version
        for prompt_id in ("p10_consistency_auditor", "p11_readthrough")
    }
    manifest = {
        "run_id": run.run_id,
        "book_slug": str(run.book["slug"]),
        "stage": "audit",
        "status": status,
        "started_at": run.started_at,
        "finished_at": finished_at,
        "config_snapshot": redacted_config(run.settings),
        "prompt_versions": prompt_versions,
        "artifacts_hash": _artifact_hashes(run.base),
        "models": {name: tier.model for name, tier in run.profile.tiers.items()},
        "stats": stats,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(_report(run, stats, calls), encoding="utf-8")
    db.transition_run(run.run_id, status)
    result.status = status
    result.warnings = list(run.warnings)
    result.cost_usd = cost
    result.calls = len(calls)
    result.cached_calls = cached
    result.coverage = coverage
    result.adherence = adherence
    result.chapters = len(run.chapters)
    result.a10_findings = int(stats.get("a10_findings", 0))
    result.a11_issues = int(stats.get("a11_issues", 0))
    result.deterministic_issues = int(stats.get("deterministic_issues", 0))
    result.repairs = int(stats.get("repair_count", 0))
    result.report_path = run_dir / "report.md"
    return result


def _select_chapters(run: _AuditRun) -> list[Any]:
    chapters = run.db.chapters(run.book_id)
    if run.options.chapter is not None:
        chapters = [chapter for chapter in chapters if int(chapter["idx"]) == run.options.chapter]
        if not chapters:
            raise ConfigError(f"chapter {run.options.chapter} does not exist")
    translated = {
        int(row["chapter_id"])
        for row in run.db.fetchall(
            """
            SELECT DISTINCT s.chapter_id FROM segments s
            JOIN translations t ON t.segment_id = s.id AND t.is_current = 1
            """
        )
    }
    selected = [chapter for chapter in chapters if int(chapter["id"]) in translated]
    if not selected:
        raise ConfigError(
            "no chapters have a current translation",
            hint="run `shz translate` first",
        )
    return selected


def _open_run(
    *,
    root: Path,
    slug: str | None,
    settings: Settings,
    profile: ModelProfile,
    provider: Provider,
    options: AuditOptions,
) -> _AuditRun:
    base = find_book_dir(root, slug, settings.project.books_dir)
    db = Database(base / "book.db")
    db.initialize()
    book = db.fetchone("SELECT * FROM books LIMIT 1")
    if book is None:
        db.close()
        raise ConfigError("the workspace has no book row", hint="re-run `shz init`")
    book_id = int(book["id"])
    db.abort_orphan_runs(book_id, settings.provider.timeout_s)
    brief = load_approved_brief(base)
    experimental = brief is None
    if brief is None:
        brief = default_brief()
    terms = approved_terms(db, book_id)
    readthrough = (
        settings.pipeline.passes.readthrough
        if options.readthrough is None
        else options.readthrough
    )
    guard = BudgetGuard(
        cap_usd=options.max_usd if options.max_usd is not None else settings.budget.max_usd,
        warn_at_fraction=settings.budget.warn_at_fraction,
        allow_overrun=not settings.budget.abort_on_exceed,
    )
    run_id = db.start_run(
        book_id=book_id,
        stage="audit",
        config_json=json.dumps(redacted_config(settings), sort_keys=True, ensure_ascii=False),
        artifacts_hash={
            "brief": brief_hash(brief),
            "glossary": _glossary_digest(terms),
        },
    )
    return _AuditRun(
        base=base,
        db=db,
        book=book,
        settings=settings,
        profile=profile,
        provider=provider,
        options=options,
        cache=ResponseCache(db),
        run_id=run_id,
        book_id=book_id,
        brief=brief,
        terms=terms,
        guard=guard,
        started_at=utc_now_iso(),
        peak=is_peak(),
        readthrough=readthrough,
        stats={
            "tariff": "peak" if is_peak() else "offpeak",
            "experimental": experimental,
            "readthrough": readthrough,
            "sample": options.sample,
        },
    )


def _record_chapter_failure(run: _AuditRun, chapter: Any, exc: Exception) -> None:
    run.db.upsert_issue(
        stage="audit",
        code="CHAPTER_FAILED",
        dedupe_key=f"ch:{chapter['idx']}:CHAPTER_FAILED",
        severity=Severity.ERROR,
        message=f"chapter {chapter['idx']} audit failed: {exc}",
        chapter_id=int(chapter["id"]),
    )
    run.warnings.append(f"chapter {chapter['idx']} audit failed: {exc}")


def run_audit(
    *,
    root: Path,
    slug: str | None,
    settings: Settings,
    profile: ModelProfile,
    provider: Provider,
    options: AuditOptions | None = None,
) -> AuditResult:
    options = options or AuditOptions()
    run: _AuditRun | None = None
    try:
        run = _open_run(
            root=root,
            slug=slug,
            settings=settings,
            profile=profile,
            provider=provider,
            options=options,
        )
        run.chapters = _select_chapters(run)
        run.book_segment_ids = {
            str(segment["id"]) for segment in run.db.segments(book_id=run.book_id)
        }
        result = AuditResult(run_id=run.run_id, warnings=list(run.warnings))
        for chapter in run.chapters:
            try:
                _audit_chapter(run, chapter)
            except BudgetPaused:
                run.paused = True
                raise
            except Exception as exc:  # noqa: BLE001 - one chapter must not abort the audit
                _record_chapter_failure(run, chapter, exc)
                continue
            run.db.update_run(
                run.run_id,
                stats={
                    "heartbeat_at": utc_now_iso(),
                    "chapters_audited": run.stats.get("chapters_audited", 0),
                },
            )
        run.stats["deterministic_issues"] = _deterministic_audit(run)
        _run_repairs(run)
        status = RunStatus.PAUSED if run.paused else RunStatus.COMPLETED
        if run.paused:
            run.stats["budget_paused"] = True
        result = _finalize(run, result, status=str(status))
        result.paused = run.paused
        if run.paused:
            raise BudgetPaused(
                "audit paused at the budget cap; state is saved",
                hint="resume with `shz audit` after raising budget.max_usd or pass --max-usd",
            )
        return result
    except BudgetPaused:
        if run is not None:
            run.stats["budget_paused"] = True
            previous = run.db.get_run(run.run_id)
            if previous is not None and previous["status"] == RunStatus.RUNNING:
                _finalize(
                    run,
                    AuditResult(run_id=run.run_id),
                    status=str(RunStatus.PAUSED),
                )
        raise
    except Exception:
        if run is not None:
            current = run.db.get_run(run.run_id)
            if current is not None and current["status"] == RunStatus.RUNNING:
                _finalize(
                    run,
                    AuditResult(run_id=run.run_id),
                    status=str(RunStatus.FAILED),
                )
        raise
    finally:
        if run is not None:
            run.db.close()
