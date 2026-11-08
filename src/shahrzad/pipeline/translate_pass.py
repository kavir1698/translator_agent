"""`shz translate`: window plan, context pack, draft/critique/revise, repair, resume."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shahrzad.agents.analyst import (
    brief_hash,
    default_brief,
    load_approved_brief,
)
from shahrzad.agents.critic import FidelityCritic, LiteraryEditor
from shahrzad.agents.ledger_curator import LedgerCurator
from shahrzad.agents.reviser import Reviser
from shahrzad.agents.translator import Translator, max_output_tokens
from shahrzad.context.glossary_match import NameList, load_proper_nouns
from shahrzad.context.packer import PackState, build_pack, render_draft
from shahrzad.core.config import Settings, redacted_config
from shahrzad.core.cost import ModelProfile, estimate_book, is_peak, select_cost_mode
from shahrzad.core.db import Database
from shahrzad.core.errors import (
    BudgetPaused,
    ConfigError,
    MissingFixture,
    SchemaValidationError,
    TransientProviderError,
)
from shahrzad.core.glossary import normalize_term_en
from shahrzad.core.ids import sha256_text, utc_now_iso
from shahrzad.core.models import (
    ChapterStatus,
    Critique,
    LedgerUpdate,
    PassKind,
    RunStatus,
    SegmentStatus,
    Severity,
    normalize_severity,
)
from shahrzad.core.paths import find_book_dir
from shahrzad.core.tokens import count_words
from shahrzad.llm.budget import BudgetGuard
from shahrzad.llm.cache import ResponseCache
from shahrzad.llm.provider import Provider
from shahrzad.llm.schema import canonical_quote
from shahrzad.persian.lint import (
    GlossaryTerm,
    LintChapter,
    LintConfig,
    LintIssue,
    LintSegment,
    LintTranslation,
    LintWindow,
    check_chapter,
    check_segment,
    check_window,
)
from shahrzad.persian.markup import normalize_translation
from shahrzad.persian.normalize import normalize_fa

from .anchors_pass import load_approved_anchors
from .glossary_ops import approved_terms, gate_blockers
from .risk import compute_risk
from .window import Window, plan_hash, plan_windows, todo_segments

CRITIC_AGENTS = {"A6", "A7", "A8"}


@dataclass
class ProgressEvent:
    """A translation milestone for a live status display.

    Counters are cumulative for the run; ``phase`` is the current work phase of
    the window being processed (drafting, reviewing, revising, repairing, checking).
    """

    kind: str
    chapter_idx: int | None = None
    chapter_position: int | None = None
    chapters_done: int = 0
    chapters_total: int = 0
    window_position: int | None = None
    chapter_windows: int = 0
    windows_done: int = 0
    windows_total: int = 0
    segments_done: int = 0
    cost_usd: float = 0.0
    phase: str | None = None
    status: str = "completed"


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass
class TranslateOptions:
    chapters: list[int] | None = None
    segments: list[str] | None = None
    windows: list[str] | None = None
    dry_run: bool = False
    exit_after: int | None = None
    max_usd: float | None = None
    cost_mode: str | None = None
    allow_overrun: bool = False
    force: bool = False
    no_cache: bool = False
    parallel_chapters: int = 1
    calibrate: bool = False
    replan: bool = False
    only_stale: bool = False
    require_anchors: bool = False
    no_anchors: bool = False
    accept_pdf_quality: bool = False
    refresh: bool = False


@dataclass
class TranslateResult:
    run_id: str | None = None
    status: str = "completed"
    paused: bool = False
    dry_run: bool = False
    mode: str = "standard"
    chapters: int = 0
    windows: int = 0
    segments_translated: int = 0
    segments_failed: int = 0
    error_issues: int = 0
    coverage: float = 0.0
    cost_usd: float = 0.0
    calls: int = 0
    cached_calls: int = 0
    cache_hit_rate: float = 0.0
    report_path: Path | None = None
    estimate: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Run:
    base: Path
    db: Database
    book: Any
    settings: Settings
    profile: ModelProfile
    provider: Provider
    options: TranslateOptions
    cache: ResponseCache
    mode: str
    run_id: str
    book_id: int
    brief: Any
    terms: list[GlossaryTerm]
    proper_nouns: NameList
    anchors: list[dict[str, Any]]
    glossary_digest: str
    glossary_terms: list[str]
    lint_config: LintConfig
    guard: BudgetGuard
    started_at: str
    peak: bool
    stats: dict[str, Any] = field(default_factory=dict)
    paused: bool = False
    stop: bool = False
    warnings: list[str] = field(default_factory=list)
    progress: ProgressCallback | None = None
    progress_status: str = "completed"
    progress_windows_done: int = 0
    progress_chapters_done: int = 0
    progress_segments_done: int = 0
    progress_lock: threading.Lock = field(default_factory=threading.Lock)

    def send_progress(self, event: ProgressEvent) -> None:
        """Never let a broken display abort a paid translation run."""
        callback = self.progress
        if callback is None:
            return
        try:
            callback(event)
        except Exception:  # noqa: BLE001 - UI failures are not run failures
            self.progress = None

    def tier(self, agent: str) -> Any:
        if agent in CRITIC_AGENTS and self.mode == "quality":
            return self.profile.tier("quality")
        if agent == "A9":
            return self.profile.tier("cheap")
        return self.profile.tier("balanced")

    def check_budget(self, *, source_tokens: int, agent: str = "A5") -> None:
        tier = self.tier(agent)
        self.guard.spent_usd = self.db.spend_for_run(self.run_id)
        self.guard.check(
            max_input_tokens=8_000 + source_tokens * 3,
            max_output_tokens=max_output_tokens(source_tokens, tier),
            pricing=tier.pricing,
            hint=(
                "the run is resumable; raise budget.max_usd or pass --allow-overrun, "
                "then run `shz translate` again"
            ),
        )


def _progress_start(run: _Run, *, chapters: int, windows: int) -> None:
    run.send_progress(
        ProgressEvent(kind="start", chapters_total=chapters, windows_total=windows)
    )


def _progress_chapter_start(run: _Run, chapter: Any, *, position: int, total: int) -> None:
    run.send_progress(
        ProgressEvent(
            kind="chapter_start",
            chapter_idx=int(chapter["idx"]),
            chapter_position=position,
            chapters_total=total,
        )
    )


def _progress_phase(run: _Run, chapter: Any, phase: str) -> None:
    run.send_progress(
        ProgressEvent(kind="phase", chapter_idx=int(chapter["idx"]), phase=phase)
    )


def _progress_window_done(
    run: _Run, chapter: Any, *, position: int, windows: int, segments: int
) -> None:
    with run.progress_lock:
        run.progress_windows_done += 1
        run.progress_segments_done += segments
        windows_done = run.progress_windows_done
        segments_done = run.progress_segments_done
    run.send_progress(
        ProgressEvent(
            kind="window_done",
            chapter_idx=int(chapter["idx"]),
            window_position=position,
            chapter_windows=windows,
            windows_done=windows_done,
            segments_done=segments_done,
            cost_usd=run.guard.spent_usd,
        )
    )


def _progress_chapter_done(run: _Run, chapter: Any, *, total: int) -> None:
    with run.progress_lock:
        run.progress_chapters_done += 1
        chapters_done = run.progress_chapters_done
        segments_done = run.progress_segments_done
    run.send_progress(
        ProgressEvent(
            kind="chapter_done",
            chapter_idx=int(chapter["idx"]),
            chapters_done=chapters_done,
            chapters_total=total,
            segments_done=segments_done,
            cost_usd=run.guard.spent_usd,
        )
    )


def _progress_finish(run: _Run) -> None:
    with run.progress_lock:
        windows_done = run.progress_windows_done
        segments_done = run.progress_segments_done
    run.send_progress(
        ProgressEvent(
            kind="finish",
            windows_done=windows_done,
            segments_done=segments_done,
            cost_usd=run.guard.spent_usd,
            status=run.progress_status,
        )
    )


def _nodes(segment: Any) -> list[dict[str, Any]]:
    raw = segment["nodes_json"]
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
    return list(raw or [])


def _glossary_digest(terms: Sequence[GlossaryTerm]) -> str:
    payload = json.dumps(
        [[term.en, term.fa, term.kind, list(term.aliases_en), list(term.aliases_fa)] for term in terms],
        sort_keys=True,
        ensure_ascii=False,
    )
    return f"sha256:{sha256_text(payload)}"


def _lint_config(settings: Settings) -> LintConfig:
    return LintConfig(
        digits=settings.persian.digits,
        keep_diacritics=settings.persian.keep_diacritics,
        ellipsis=settings.persian.ellipsis,
        zwnj_repair=settings.persian.zwnj_repair,
    )


def _lint_objects(
    segments: Sequence[Any], translations: dict[str, Any]
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


def _persist_lint(
    run: _Run,
    issues: Sequence[LintIssue],
    *,
    scope: str,
    ref: str,
    chapter_id: int | None,
    segment_id: str | None = None,
) -> None:
    for issue in issues:
        target_segment = issue.context.get("segment_id") if issue.context else None
        run.db.upsert_issue(
            stage="qa",
            code=issue.code,
            dedupe_key=issue.dedupe_key(scope, ref),
            severity=str(issue.severity),
            message=issue.message,
            segment_id=segment_id or target_segment,
            chapter_id=chapter_id,
            quote_source=issue.quote,
            context=issue.context,
        )


def _current_map(db: Database, segments: Sequence[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for segment in segments:
        row = db.current_translation(str(segment["id"]))
        if row is not None:
            result[str(segment["id"])] = row
    return result


def _lint_errors_for(
    segment: Any,
    translation: Any,
    terms: list[GlossaryTerm],
    config: LintConfig,
) -> list[LintIssue]:
    lint_segment, lint_translation = _lint_objects([segment], {str(segment["id"]): translation})
    issues = check_segment(lint_segment[0], lint_translation[str(segment["id"])], terms, config)
    return [issue for issue in issues if str(issue.severity) in {Severity.ERROR, Severity.CRITICAL}]


def _status_after(
    db: Database, segment: Any, *, has_error: bool, force: bool = False
) -> None:
    current = str(segment["status"])
    segment_id = str(segment["id"])
    if current == SegmentStatus.SKIPPED:
        return
    if current == SegmentStatus.DONE and not force:
        pass
    elif current in {SegmentStatus.PENDING, SegmentStatus.FAILED}:
        db.transition_segment(segment_id, SegmentStatus.DRAFTED)
    elif current == SegmentStatus.DONE and force or current == SegmentStatus.FLAGGED:
        db.transition_segment(segment_id, SegmentStatus.REVISED)
    if has_error:
        latest = db.get_segment(segment_id)
        assert latest is not None
        if latest["status"] == SegmentStatus.DRAFTED:
            db.transition_segment(segment_id, SegmentStatus.REVISED)
        if db.get_segment(segment_id)["status"] != SegmentStatus.FLAGGED:  # type: ignore[index]
            db.transition_segment(segment_id, SegmentStatus.FLAGGED)
    else:
        latest = db.get_segment(segment_id)
        assert latest is not None
        if latest["status"] in {SegmentStatus.DRAFTED, SegmentStatus.REVISED, SegmentStatus.FLAGGED}:
            db.transition_segment(segment_id, SegmentStatus.DONE)
        for row in db.issues_for(segment_id=segment_id, status="open"):
            if str(row["stage"]) == "translate":
                db.set_issue_status(int(row["id"]), "resolved")


def _resolve_critique_issues(run: _Run, segment_id: str) -> None:
    for row in run.db.issues_for(segment_id=segment_id, status="open"):
        if str(row["stage"]) == "critique":
            run.db.set_issue_status(int(row["id"]), "resolved")


def _store_translation(
    run: _Run,
    *,
    segment: Any,
    item: dict[str, Any],
    pass_kind: str,
    model: str,
    prompt_hash: str | None,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    latency_ms: int,
    force: bool = False,
) -> tuple[Any, list[LintIssue]]:
    normalized = normalize_translation(
        str(item["fa_markup"]),
        _nodes(segment),
        digits=run.settings.persian.digits,
        keep_diacritics=run.settings.persian.keep_diacritics,
        ellipsis=run.settings.persian.ellipsis,
    )
    db = run.db
    db.add_translation(
        segment_id=str(segment["id"]),
        pass_kind=pass_kind,
        fa_text=normalized.fa_text,
        fa_markup=normalized.fa_markup,
        model=model,
        notes={"notes": str(item.get("notes", ""))},
        glossary_hash=run.glossary_digest,
        glossary_terms=run.glossary_terms,
        prompt_hash=prompt_hash,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        latency_ms=latency_ms,
    )
    translation = db.current_translation(str(segment["id"]))
    if normalized.problems:
        for problem in normalized.problems:
            db.upsert_issue(
                stage="qa",
                code="L02",
                dedupe_key=f"seg:{segment['id']}:L02:{sha256_text(problem.message)[:12]}",
                severity=Severity.ERROR,
                message=f"markup invalid: {problem.message}",
                segment_id=str(segment["id"]),
                chapter_id=int(segment["chapter_id"]),
                quote_source=normalized.fa_markup[:120],
                context={"problem": problem.code},
            )
    return translation, []


def _persist_suggestions(run: _Run, chapter_segments: Sequence[Any], suggestions: Sequence[Any]) -> None:
    valid_ids = {str(segment["id"]) for segment in chapter_segments}
    for suggestion in suggestions:
        if suggestion.segment_id not in valid_ids:
            continue
        run.db.add_glossary_suggestion(
            book_id=run.book_id,
            segment_id=suggestion.segment_id,
            term_en=suggestion.en,
            term_fa=suggestion.fa,
            kind=suggestion.kind,
            reason=suggestion.reason,
        )


def _agent_issue_dicts(
    critique: Critique, *, agent: str, segment_id: str, source_text: str, target_text: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for issue in critique.issues:
        if issue.segment_id != segment_id:
            continue
        verified = True
        if issue.quote_source:
            quote = canonical_quote(issue.quote_source)
            verified = bool(quote) and quote in canonical_quote(source_text)
        if issue.quote_target:
            quote_target = canonical_quote(issue.quote_target)
            verified = verified and bool(quote_target) and quote_target in canonical_quote(target_text)
        result.append(
            {
                "agent": agent,
                "segment_id": issue.segment_id,
                "type": issue.type,
                "severity": issue.severity,
                "quote_source": issue.quote_source,
                "quote_target": issue.quote_target,
                "problem": issue.problem,
                "suggested_fix": issue.suggested_fix,
                "verified": verified,
            }
        )
    return result


def _persist_agent_issues(
    run: _Run, chapter_id: int, findings: Sequence[dict[str, Any]]
) -> int:
    count = 0
    for finding in findings:
        severity = normalize_severity(str(finding["severity"]))
        context = {
            "agent": finding["agent"],
            "quote_verified": bool(finding.get("verified", True)),
            "suggested_fix": finding.get("suggested_fix", ""),
        }
        if not finding.get("verified", True):
            if severity in {Severity.ERROR, Severity.CRITICAL}:
                severity = Severity.WARN
            context["unverified_quote"] = True
        code = f"{finding['agent']}_{finding['type']}"
        dedupe = sha256_text(
            f"{finding.get('quote_source', '')}|{finding.get('quote_target', '')}|{finding['problem']}"
        )[:12]
        run.db.upsert_issue(
            stage="critique",
            code=code,
            dedupe_key=f"seg:{finding['segment_id']}:{code}:{dedupe}",
            severity=severity,
            message=str(finding["problem"]),
            segment_id=str(finding["segment_id"]),
            chapter_id=chapter_id,
            quote_source=str(finding.get("quote_source", "")),
            quote_target=str(finding.get("quote_target", "")),
            context=context,
        )
        count += 1
    return count


def _regression_reason(
    run: _Run, segment: Any, old_markup: str, new_markup: str
) -> str | None:
    old = normalize_translation(
        old_markup,
        _nodes(segment),
        digits=run.settings.persian.digits,
        keep_diacritics=run.settings.persian.keep_diacritics,
        ellipsis=run.settings.persian.ellipsis,
    )
    new = normalize_translation(
        new_markup,
        _nodes(segment),
        digits=run.settings.persian.digits,
        keep_diacritics=run.settings.persian.keep_diacritics,
        ellipsis=run.settings.persian.ellipsis,
    )
    if new.problems and not old.problems:
        return "markup"
    if not new.fa_text.strip():
        return "empty"
    old_row = run.db.current_translation(str(segment["id"]))
    if old_row is not None:
        old_errors = len(
            _lint_errors_for(segment, old_row, run.terms, run.lint_config)
        )
        new_errors = len(
            _lint_errors_for(
                segment,
                {"fa_text": new.fa_text, "fa_markup": new.fa_markup},
                run.terms,
                run.lint_config,
            )
        )
        if new_errors > old_errors:
            return "new_lint_error"
    source_words = max(1, count_words(str(segment["source_text"])))
    old_ratio = count_words(old.fa_text) / source_words
    new_ratio = count_words(new.fa_text) / source_words
    if 0.55 <= old_ratio <= 1.70 and not 0.55 <= new_ratio <= 1.70:
        return "length_outlier"
    return None


def _chapter_summary(run: _Run, chapter: Any) -> str:
    row = run.db.get_chapter_summary(int(chapter["id"]))
    return str(row["summary_en"]) if row is not None else ""


def _state(run: _Run, chapter: Any) -> PackState:
    ledger_rows = run.db.ledger_entries(run.book_id)
    cast = tuple(row for row in ledger_rows if str(row["kind"]) == "character")
    fresh = run.db.get_chapter(int(chapter["id"]))
    rolling = str(fresh["rolling_summary_fa"] or "") if fresh is not None else ""
    return PackState(
        brief=run.brief,
        glossary_terms=tuple(run.terms),
        proper_nouns=run.proper_nouns,
        anchors=tuple(run.anchors),
        chapter_summary=_chapter_summary(run, chapter),
        rolling_summary=rolling,
        ledger=tuple(ledger_rows),
        cast=cast,
    )


def _refresh_rolling_summary(run: _Run, chapter: Any) -> None:
    segments = run.db.segments(chapter_id=int(chapter["id"]))
    texts = [
        str(row["fa_text"])
        for segment in segments
        if (row := run.db.current_translation(str(segment["id"]))) is not None
    ]
    if not texts:
        return
    summary = " ".join(texts[-3:])
    if len(summary) > 500:
        summary = summary[:497].rstrip() + "…"
    run.db.set_chapter_rolling_summary(int(chapter["id"]), summary)


def _chapter_title_fa(run: _Run, chapter: Any, segments: Sequence[Any]) -> str:
    for segment in segments:
        if str(segment["kind"]) == "heading":
            row = run.db.current_translation(str(segment["id"]))
            if row is not None and str(row["fa_text"]).strip():
                return normalize_fa(str(row["fa_text"])).strip()
    if str(chapter["kind"]) == "front":
        return "پیشگفتار"
    number = str(int(chapter["idx"])).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))
    return f"فصل {number}"


def _curate_ledger(run: _Run, chapter: Any, chapter_segments: Sequence[Any]) -> None:
    summary = _chapter_summary(run, chapter)
    texts = [
        str(row["fa_text"])
        for segment in chapter_segments
        if (row := run.db.current_translation(str(segment["id"]))) is not None
    ]
    if not texts:
        return
    excerpt = "\n\n".join([*texts[:2], *texts[-2:]])
    notes = [
        {"segment_id": str(segment["id"]), "note": str(row["notes_json"])}
        for segment in chapter_segments
        if (row := run.db.current_translation(str(segment["id"]))) is not None
        and row["notes_json"] not in ("{}", "")
    ]
    suggestions = [
        row
        for row in run.db.pending_glossary_suggestions(run.book_id)
        if str(row["chapter_id"]) == str(chapter["id"])
    ]
    new_terms = json.dumps(
        [
            {"en": str(row["term_en"]), "fa": str(row["term_fa"]), "kind": str(row["kind"])}
            for row in suggestions
        ],
        ensure_ascii=False,
    )
    existing = "\n".join(
        f"- {row['kind']}: {row['key']}" for row in run.db.ledger_entries(run.book_id)[-30:]
    )
    curator = LedgerCurator()
    try:
        run.check_budget(source_tokens=max(1, len(excerpt) // 4), agent="A9")
        outcome = curator.curate(
            provider=run.provider,
            tier=run.tier("A9"),
            db=run.db,
            run_id=run.run_id,
            cache=run.cache,
            chapter_index=int(chapter["idx"]),
            chapter_summary=summary,
            chapter_excerpt_fa=excerpt,
            new_terms=new_terms,
            translator_notes=json.dumps(notes, ensure_ascii=False),
            existing_ledger=existing or "(empty)",
        )
    except (MissingFixture, SchemaValidationError, TransientProviderError) as exc:
        run.db.upsert_issue(
            stage="translate",
            code="A9_SKIPPED",
            dedupe_key=f"ch:{chapter['idx']}:A9_SKIPPED",
            severity=Severity.WARN,
            message=f"ledger curation skipped for chapter {chapter['idx']}: {exc}",
            chapter_id=int(chapter["id"]),
        )
        return
    _apply_ledger(run, chapter, outcome.value)


def _apply_ledger(run: _Run, chapter: Any, update: LedgerUpdate) -> None:
    chapter_idx = int(chapter["idx"])
    for character in update.characters:
        run.db.upsert_ledger(
            book_id=run.book_id,
            chapter_idx=chapter_idx,
            kind="character",
            key=normalize_term_en(character.en),
            value=character.model_dump(),
        )
    for term in update.terms:
        en = str(term.get("en", ""))
        if not en:
            continue
        run.db.upsert_ledger(
            book_id=run.book_id,
            chapter_idx=chapter_idx,
            kind="term",
            key=normalize_term_en(en),
            value=term,
        )
    for decision in update.decisions:
        key = str(decision.get("key", "")).strip()
        if not key:
            continue
        run.db.upsert_ledger(
            book_id=run.book_id,
            chapter_idx=chapter_idx,
            kind="decision",
            key=key,
            value={"value": decision.get("value", "")},
        )
    for question in update.open_questions:
        run.db.upsert_ledger(
            book_id=run.book_id,
            chapter_idx=chapter_idx,
            kind="question",
            key=sha256_text(question)[:12],
            value={"question": question},
        )
    for correction in update.corrections:
        key = normalize_term_en(str(correction.get("key", "")))
        if not key:
            continue
        run.db.upsert_issue(
            stage="translate",
            code="LEDGER_CORRECTION",
            dedupe_key=f"ch:{chapter_idx}:LEDGER_CORRECTION:{sha256_text(json.dumps(correction, sort_keys=True))[:12]}",
            severity=Severity.WARN,
            message=f"ledger correction suggested: {correction}",
            chapter_id=int(chapter["id"]),
        )
    run.db.sync_ledger_artifact(run.base, run.book_id)


def _window_lint(
    run: _Run,
    window: Window,
    chapter_segments: Sequence[Any],
    translations: dict[str, Any],
) -> None:
    lint_segments, lint_translations = _lint_objects(chapter_segments, translations)
    for segment in window.segments:
        issue_set = check_segment(
            next(item for item in lint_segments if item.id == str(segment["id"])),
            lint_translations.get(str(segment["id"])),
            run.terms,
            run.lint_config,
        )
        _persist_lint(
            run,
            issue_set,
            scope="segment",
            ref=str(segment["id"]),
            chapter_id=int(segment["chapter_id"]),
            segment_id=str(segment["id"]),
        )
    ids = set(window.segment_ids)
    window_trs = {
        key: value for key, value in lint_translations.items() if key in ids
    }
    window_issues = check_window(
        LintWindow(
            id=window.id,
            segments=[item for item in lint_segments if item.id in ids],
        ),
        window_trs,
        run.terms,
    )
    _persist_lint(
        run,
        window_issues,
        scope="window",
        ref=window.id,
        chapter_id=int(window.segments[0]["chapter_id"]) if window.segments else None,
    )
    keep = {issue.dedupe_key("window", window.id) for issue in window_issues}
    prefix = f"seg:{window.id}:"
    for row in run.db.fetchall(
        "SELECT id, dedupe_key FROM issues WHERE stage = 'qa' AND status = 'open' "
        "AND dedupe_key LIKE ?",
        (f"{prefix}%",),
    ):
        if str(row["dedupe_key"]) not in keep:
            run.db.set_issue_status(int(row["id"]), "resolved")


def _window_issue_count(db: Database, window: Window, chapter_id: int) -> int:
    marks = ",".join("?" for _ in window.segment_ids)
    if not window.segment_ids:
        return 0
    row = db.fetchone(
        f"SELECT COUNT(*) FROM issues WHERE status = 'open' AND chapter_id = ? "
        f"AND segment_id IN ({marks})",
        (chapter_id, *window.segment_ids),
    )
    return int(row[0]) if row else 0


def _reconcile_lint_issues(run: _Run, segment: Any) -> None:
    """Resolve open qa issues that the current translation no longer reproduces."""
    translation = run.db.current_translation(str(segment["id"]))
    if translation is None:
        return
    current = check_segment(
        LintSegment(
            id=str(segment["id"]),
            kind=str(segment["kind"]),
            source_text=str(segment["source_text"]),
            source_html=str(segment["source_html"]),
            nodes=_nodes(segment),
            status=str(segment["status"]),
        ),
        LintTranslation(
            fa_text=str(translation["fa_text"]), fa_markup=str(translation["fa_markup"])
        ),
        run.terms,
        run.lint_config,
    )
    keep = {issue.dedupe_key("segment", str(segment["id"])) for issue in current}
    for row in run.db.issues_for(segment_id=str(segment["id"]), status="open"):
        if str(row["stage"]) != "qa":
            continue
        if str(row["dedupe_key"]) not in keep:
            run.db.set_issue_status(int(row["id"]), "resolved")


def _finalize_segment(
    run: _Run, segment: Any, *, force: bool, window: Window | None = None
) -> tuple[bool, int]:
    if run.db.current_translation(str(segment["id"])) is None:
        return True, 1
    _reconcile_lint_issues(run, segment)
    issues = run.db.issues_for(segment_id=str(segment["id"]), status="open")
    window_issues: list[Any] = []
    if window is not None:
        window_issues = run.db.fetchall(
            "SELECT * FROM issues WHERE stage = 'qa' AND status = 'open' "
            "AND segment_id IS NULL AND dedupe_key LIKE ?",
            (f"seg:{window.id}:%",),
        )
    has_error = any(
        str(issue["severity"]) in {"error", "critical"}
        and str(issue["stage"]) == "qa"
        for issue in [*issues, *window_issues]
    )
    _status_after(run.db, segment, has_error=has_error, force=force)
    latest = run.db.get_segment(str(segment["id"]))
    if latest is not None:
        suggestions = run.db.scalar(
            "SELECT COUNT(*) FROM glossary_suggestions WHERE segment_id = ? AND status = 'pending'",
            (str(segment["id"]),),
        )
        risk = compute_risk(
            latest,
            [*issues, *window_issues],
            window_issue_count=len(window_issues),
            unapproved_suggestions=int(suggestions or 0),
        )
        run.db.set_segment_risk(str(segment["id"]), risk)
    return has_error, 0


def _process_window(
    run: _Run, chapter: Any, window: Window, chapter_segments: Sequence[Any]
) -> tuple[int, int]:
    """Translate one window. Returns (segments processed, failures)."""
    options = run.options
    allowed = set(options.segments) if options.segments else None
    todo = todo_segments(window, force=options.force)
    if allowed is not None:
        todo = [segment for segment in todo if str(segment["id"]) in allowed]
    if options.only_stale:
        todo = [
            segment
            for segment in window.segments
            if str(segment["status"]) != SegmentStatus.SKIPPED
            and (allowed is None or str(segment["id"]) in allowed)
            and (
                (row := run.db.current_translation(str(segment["id"]))) is None
                or str(row["glossary_hash"] or "") != run.glossary_digest
            )
        ]
    if not todo:
        return 0, 0
    source_tokens = sum(max(1, int(segment["tokens_est"])) for segment in todo)
    run.check_budget(source_tokens=source_tokens)

    translations = _current_map(run.db, window.segments)
    prev_translations = {
        key: value
        for segment in window.overlap
        if (value := run.db.current_translation(str(segment["id"]))) is not None
        for key in [str(segment["id"])]
    }
    variables = build_pack(
        window_segments=window.segments,
        todo_segments=todo,
        state=_state(run, chapter),
        prev_segments=window.overlap,
        prev_translations=prev_translations,
    )

    _progress_phase(run, chapter, "drafting")
    translator = Translator()
    outcome = translator.translate(
        provider=run.provider,
        tier=run.tier("A5"),
        db=run.db,
        run_id=run.run_id,
        cache=run.cache,
        variables=variables,
        segments=todo,
    )
    items = {str(item["id"]): item for item in outcome.segments}
    share_in = outcome.result.tokens_in // max(1, len(items))
    share_out = outcome.result.tokens_out // max(1, len(items))
    share_cost = outcome.result.cost_usd / max(1, len(items))
    processed = 0
    failures = 0
    for segment in todo:
        item = items.get(str(segment["id"]))
        if item is None:
            run.db.upsert_issue(
                stage="translate",
                code="SEGMENT_MISSING",
                dedupe_key=f"seg:{segment['id']}:SEGMENT_MISSING",
                severity=Severity.ERROR,
                message="the model did not return this segment after retry",
                segment_id=str(segment["id"]),
                chapter_id=int(segment["chapter_id"]),
            )
            run.db.transition_segment(str(segment["id"]), SegmentStatus.FAILED)
            failures += 1
            continue
        _store_translation(
            run,
            segment=segment,
            item=item,
            pass_kind=PassKind.DRAFT,
            model=outcome.result.model,
            prompt_hash=outcome.result.prompt_hash,
            tokens_in=share_in,
            tokens_out=share_out,
            cost_usd=share_cost,
            latency_ms=outcome.result.latency_ms // max(1, len(items)),
            force=options.force,
        )
        processed += 1
    _persist_suggestions(run, chapter_segments, outcome.result.value.glossary_suggestions)

    translations = _current_map(run.db, window.segments)
    lint_segments, lint_translations = _lint_objects(chapter_segments, translations)
    for segment in todo:
        segment_lint = next(item for item in lint_segments if item.id == str(segment["id"]))
        found = check_segment(
            segment_lint, lint_translations.get(str(segment["id"])), run.terms, run.lint_config
        )
        _persist_lint(
            run,
            found,
            scope="segment",
            ref=str(segment["id"]),
            chapter_id=int(segment["chapter_id"]),
            segment_id=str(segment["id"]),
        )

    run.check_budget(source_tokens=source_tokens)
    _progress_phase(run, chapter, "reviewing")
    findings: list[dict[str, Any]] = []
    fidelity_needed = any(
        issue.code == "L08" and str(issue.severity) == Severity.ERROR
        for segment in todo
        for issue in check_segment(
            next(item for item in lint_segments if item.id == str(segment["id"])),
            lint_translations.get(str(segment["id"])),
            run.terms,
            run.lint_config,
        )
    )
    if run.mode != "economy" and run.settings.pipeline.critics.fidelity:
        draft = render_draft(todo, translations)
        fidelity = FidelityCritic().review(
            provider=run.provider,
            tier=run.tier("A6"),
            db=run.db,
            run_id=run.run_id,
            cache=run.cache,
            variables=variables,
            segments=todo,
            draft=draft,
        )
        for segment in todo:
            row = translations.get(str(segment["id"]))
            findings.extend(
                _agent_issue_dicts(
                    fidelity.value,
                    agent="A6",
                    segment_id=str(segment["id"]),
                    source_text=str(segment["source_text"]),
                    target_text=str(row["fa_text"]) if row is not None else "",
                )
            )
    elif run.mode == "economy" and run.settings.pipeline.critics.fidelity:
        draft = render_draft(todo, translations)
        if fidelity_needed:
            fidelity = FidelityCritic().review(
                provider=run.provider,
                tier=run.tier("A6"),
                db=run.db,
                run_id=run.run_id,
                cache=run.cache,
                variables=variables,
                segments=todo,
                draft=draft,
            )
            for segment in todo:
                row = translations.get(str(segment["id"]))
                findings.extend(
                    _agent_issue_dicts(
                        fidelity.value,
                        agent="A6",
                        segment_id=str(segment["id"]),
                        source_text=str(segment["source_text"]),
                        target_text=str(row["fa_text"]) if row is not None else "",
                    )
                )
    if run.mode != "economy" and run.settings.pipeline.critics.style:
        draft = render_draft(todo, translations)
        style = LiteraryEditor().review(
            provider=run.provider,
            tier=run.tier("A7"),
            db=run.db,
            run_id=run.run_id,
            cache=run.cache,
            variables=variables,
            segments=todo,
            draft=draft,
        )
        for segment in todo:
            row = translations.get(str(segment["id"]))
            findings.extend(
                _agent_issue_dicts(
                    style.value,
                    agent="A7",
                    segment_id=str(segment["id"]),
                    source_text=str(segment["source_text"]),
                    target_text=str(row["fa_text"]) if row is not None else "",
                )
            )
    _persist_agent_issues(run, int(chapter["id"]), findings)

    _progress_phase(run, chapter, "revising")
    actionable = [
        finding
        for finding in findings
        if finding["severity"] in {"major", "critical"} and finding.get("verified", True)
    ]
    rounds = 0
    if actionable and rounds < run.settings.pipeline.critics.max_revision_rounds:
        rounds += 1
        run.check_budget(source_tokens=source_tokens, agent="A8")
        draft = render_draft(todo, translations)
        revised = Reviser().revise(
            provider=run.provider,
            tier=run.tier("A8"),
            db=run.db,
            run_id=run.run_id,
            cache=run.cache,
            variables=variables,
            segments=todo,
            draft=draft,
            issues=actionable,
        )
        revised_items = {str(item.id): item.model_dump() for item in revised.value.segments}
        for segment in todo:
            item = revised_items.get(str(segment["id"]))
            if item is None:
                continue
            old = run.db.current_translation(str(segment["id"]))
            if old is None:
                continue
            reason = _regression_reason(run, segment, str(old["fa_markup"]), str(item["fa_markup"]))
            if reason and str(item["fa_markup"]) != str(old["fa_markup"]):
                run.db.upsert_issue(
                    stage="translate",
                    code="revision_regressed",
                    dedupe_key=f"seg:{segment['id']}:revision_regressed:{reason}",
                    severity=Severity.WARN,
                    message=f"revision rejected by the regression guard ({reason})",
                    segment_id=str(segment["id"]),
                    chapter_id=int(segment["chapter_id"]),
                    context={"reason": reason},
                )
                continue
            _store_translation(
                run,
                segment=segment,
                item=item,
                pass_kind=PassKind.REVISION,
                model=revised.model,
                prompt_hash=revised.prompt_hash,
                tokens_in=revised.tokens_in // max(1, len(todo)),
                tokens_out=revised.tokens_out // max(1, len(todo)),
                cost_usd=revised.cost_usd / max(1, len(todo)),
                latency_ms=revised.latency_ms // max(1, len(todo)),
            )
            _resolve_critique_issues(run, str(segment["id"]))

    # Deterministic repair ladder: up to two rounds for segments with lint errors.
    _progress_phase(run, chapter, "repairing")
    for _ in range(2):
        translations = _current_map(run.db, window.segments)
        lint_segments, lint_translations = _lint_objects(chapter_segments, translations)
        failing: list[Any] = []
        lint_messages: list[str] = []
        for segment in todo:
            if run.db.current_translation(str(segment["id"])) is None:
                continue
            segment_lint = next(item for item in lint_segments if item.id == str(segment["id"]))
            found = check_segment(
                segment_lint, lint_translations.get(str(segment["id"])), run.terms, run.lint_config
            )
            errors = [
                issue
                for issue in found
                if str(issue.severity) in {Severity.ERROR, Severity.CRITICAL}
            ]
            if errors:
                failing.append(segment)
                lint_messages.extend(f"{segment['id']}: {issue.code} {issue.message}" for issue in errors)
        ids = set(window.segment_ids)
        window_found = check_window(
            LintWindow(
                id=window.id,
                segments=[item for item in lint_segments if item.id in ids],
            ),
            {key: value for key, value in lint_translations.items() if key in ids},
            run.terms,
        )
        for issue in window_found:
            if str(issue.severity) not in {Severity.ERROR, Severity.CRITICAL}:
                continue
            if not failing:
                failing = [segment for segment in todo if run.db.current_translation(str(segment["id"]))]
            lint_messages.append(f"{window.id}: {issue.code} {issue.message}")
        if not failing:
            break
        run.check_budget(source_tokens=sum(max(1, int(s["tokens_est"])) for s in failing))
        repair_vars = build_pack(
            window_segments=window.segments,
            todo_segments=failing,
            state=_state(run, chapter),
            prev_segments=window.overlap,
            prev_translations=prev_translations,
        )
        repair = Reviser().revise(
            provider=run.provider,
            tier=run.tier("A8"),
            db=run.db,
            run_id=run.run_id,
            cache=run.cache,
            variables=repair_vars,
            segments=failing,
            draft=render_draft(failing, translations),
            issues=[],
            repair_only=True,
            lint_errors="; ".join(lint_messages),
        )
        repaired = {str(item.id): item.model_dump() for item in repair.value.segments}
        for segment in failing:
            item = repaired.get(str(segment["id"]))
            if item is None:
                continue
            old = run.db.current_translation(str(segment["id"]))
            if old is None:
                continue
            reason = _regression_reason(run, segment, str(old["fa_markup"]), str(item["fa_markup"]))
            if reason and str(item["fa_markup"]) != str(old["fa_markup"]):
                continue
            _store_translation(
                run,
                segment=segment,
                item=item,
                pass_kind=PassKind.REPAIR,
                model=repair.model,
                prompt_hash=repair.prompt_hash,
                tokens_in=repair.tokens_in // max(1, len(failing)),
                tokens_out=repair.tokens_out // max(1, len(failing)),
                cost_usd=repair.cost_usd / max(1, len(failing)),
                latency_ms=repair.latency_ms // max(1, len(failing)),
            )
            _resolve_critique_issues(run, str(segment["id"]))

    _progress_phase(run, chapter, "checking")
    translations = _current_map(run.db, window.segments)
    _window_lint(run, window, chapter_segments, translations)
    for segment in todo:
        _finalize_segment(run, segment, force=options.force, window=window)
    for row in run.db.fetchall(
        "SELECT id FROM issues WHERE stage = 'translate' AND status = 'open' "
        "AND code IN ('WINDOW_FAILED','SEGMENT_MISSING') AND dedupe_key LIKE ?",
        (f"seg:{window.id}:%",),
    ):
        run.db.set_issue_status(int(row["id"]), "resolved")
    return processed, failures


def _process_chapter(
    run: _Run, chapter: Any, *, position: int = 1, total: int = 1
) -> dict[str, Any]:
    chapter_id = int(chapter["id"])
    chapter_segments = run.db.segments(chapter_id=chapter_id)
    if not any(str(segment["status"]) != SegmentStatus.SKIPPED for segment in chapter_segments):
        return {"chapter": int(chapter["idx"]), "windows": 0, "segments": 0}
    settings_window = run.settings.pipeline.window
    windows = plan_windows(
        chapter_segments,
        chapter_id=chapter_id,
        chapter_idx=int(chapter["idx"]),
        max_paragraphs=settings_window.max_paragraphs,
        max_source_tokens=settings_window.max_source_tokens,
        overlap_paragraphs=settings_window.overlap_paragraphs,
    )
    if run.options.windows:
        allowed_windows = set(run.options.windows)
        windows = [window for window in windows if window.id in allowed_windows]
    if not windows:
        return {"chapter": int(chapter["idx"]), "windows": 0, "segments": 0}
    _progress_chapter_start(run, chapter, position=position, total=total)
    current_status = str(chapter["status"])
    if current_status not in {
        ChapterStatus.TRANSLATED,
        ChapterStatus.AUDITED,
        ChapterStatus.REVIEWED,
        ChapterStatus.FINAL,
    }:
        run.db.transition_chapter(chapter_id, ChapterStatus.TRANSLATING)
    translated = 0
    processed_windows = 0
    try:
        for index, window in enumerate(windows, start=1):
            if run.stop or run.paused:
                break
            try:
                processed, failures = _process_window(run, chapter, window, chapter_segments)
            except BudgetPaused:
                raise
            except Exception as exc:  # noqa: BLE001 - one window must not abort the chapter
                run.db.upsert_issue(
                    stage="translate",
                    code="WINDOW_FAILED",
                    dedupe_key=f"seg:{window.id}:WINDOW_FAILED",
                    severity=Severity.ERROR,
                    message=f"window {window.id} failed: {exc}",
                    chapter_id=chapter_id,
                )
                run.warnings.append(f"window {window.id} failed: {exc}")
                continue
            translated += processed
            if processed:
                processed_windows += 1
                run.stats["windows"] = int(run.stats.get("windows", 0)) + 1
            _progress_window_done(
                run, chapter, position=index, windows=len(windows), segments=processed
            )
            if index % 20 == 0:
                _refresh_rolling_summary(run, chapter)
            run.db.update_run(
                run.run_id,
                stats={
                    "heartbeat_at": utc_now_iso(),
                    "windows": run.stats.get("windows", 0),
                    "segments_translated": run.stats.get("segments_translated", 0) + processed,
                },
            )
            if run.options.exit_after and processed_windows >= run.options.exit_after:
                run.stop = True
                break
    except BudgetPaused:
        run.paused = True
        raise
    if not run.paused:
        _progress_chapter_done(run, chapter, total=total)
    _refresh_rolling_summary(run, chapter)
    latest_chapter = run.db.get_chapter(chapter_id)
    if latest_chapter is not None and latest_chapter["status"] == ChapterStatus.TRANSLATING:
        run.db.transition_chapter(chapter_id, ChapterStatus.TRANSLATED)
    run.db.set_chapter_title_fa(chapter_id, _chapter_title_fa(run, chapter, chapter_segments))
    remaining = run.db.segments(chapter_id=chapter_id)
    if all(
        str(segment["status"]) in {SegmentStatus.DONE, SegmentStatus.FLAGGED, SegmentStatus.SKIPPED}
        for segment in remaining
    ):
        for row in run.db.fetchall(
            "SELECT id FROM issues WHERE stage = 'translate' AND status = 'open' "
            "AND code = 'CHAPTER_FAILED' AND chapter_id = ?",
            (chapter_id,),
        ):
            run.db.set_issue_status(int(row["id"]), "resolved")
    if (
        translated > 0
        and not run.paused
        and not run.options.dry_run
        and run.options.parallel_chapters <= 1
    ):
        _curate_ledger(run, chapter, chapter_segments)
    _chapter_drift(run, chapter, chapter_segments)
    return {
        "chapter": int(chapter["idx"]),
        "windows": processed_windows,
        "segments": translated,
        "status": str(run.db.get_chapter(chapter_id)["status"]),  # type: ignore[index]
    }


def _chapter_drift(run: _Run, chapter: Any, segments: Sequence[Any]) -> None:
    translations = _current_map(run.db, segments)
    lint_segments, lint_translations = _lint_objects(segments, translations)
    baseline = run.stats.get("baseline_metrics")
    issues = check_chapter(
        LintChapter(
            idx=int(chapter["idx"]),
            segments=lint_segments,
            translations=lint_translations,
            baseline=baseline if isinstance(baseline, dict) else {},
        )
    )
    _persist_lint(run, issues, scope="chapter", ref=str(chapter["idx"]), chapter_id=int(chapter["id"]))
    if not isinstance(baseline, dict):
        metrics = _metrics_from(lint_segments, lint_translations)
        run.stats["baseline_metrics"] = metrics


def _metrics_from(
    segments: Sequence[LintSegment], translations: dict[str, LintTranslation]
) -> dict[str, float]:
    texts = [translations[segment.id].fa_text for segment in segments if segment.id in translations]
    if not texts:
        return {}
    words = sum(count_words(text) for text in texts)
    return {
        "mean_sentence_words": words / max(1, len(texts)),
        "dialogue_ratio": sum(text.count("«") for text in texts) / max(1, len(texts)),
        "unique_word_ratio": len({word for text in texts for word in text.split()}) / max(1, words),
    }


def _select_chapters(run: _Run) -> list[Any]:
    chapters = run.db.chapters(run.book_id)
    if run.options.segments:
        marks = ",".join("?" for _ in run.options.segments)
        rows = run.db.fetchall(
            f"SELECT DISTINCT chapter_id FROM segments WHERE id IN ({marks})",
            tuple(run.options.segments),
        )
        wanted_ids = {int(row["chapter_id"]) for row in rows}
        chapters = [chapter for chapter in chapters if int(chapter["id"]) in wanted_ids]
    elif run.options.windows:
        wanted_idx = set()
        for window_id in run.options.windows:
            match = re.match(r"c(\d+)\.", window_id)
            if match:
                wanted_idx.add(int(match.group(1)))
        chapters = [chapter for chapter in chapters if int(chapter["idx"]) in wanted_idx]
    elif run.options.chapters:
        wanted = set(run.options.chapters)
        chapters = [chapter for chapter in chapters if int(chapter["idx"]) in wanted]
    return [chapter for chapter in chapters if db_has_translatable(run.db, int(chapter["id"]))]


def db_has_translatable(db: Database, chapter_id: int) -> bool:
    return any(
        str(segment["status"]) != SegmentStatus.SKIPPED for segment in db.segments(chapter_id=chapter_id)
    )


def _scope_label(chapters: Sequence[Any]) -> str:
    return "chapters:" + ",".join(str(int(chapter["idx"])) for chapter in chapters)


def _mode_for(run: _Run, chapters: Sequence[Any]) -> tuple[str, str]:
    requested = run.options.cost_mode or run.settings.pipeline.cost_mode
    words = sum(int(chapter["word_count"] or 0) for chapter in chapters)
    if requested == "auto":
        return select_cost_mode(words, run.profile, run.guard.cap_usd, peak=run.peak)
    if requested not in {"economy", "standard", "quality"}:
        raise ConfigError(f"unknown cost mode: {requested}")
    return requested, f"explicit cost mode {requested}"


def _open_run(
    *,
    root: Path,
    slug: str | None,
    settings: Settings,
    profile: ModelProfile,
    provider: Provider,
    options: TranslateOptions,
) -> tuple[_Run, TranslateResult]:
    base = find_book_dir(root, slug, settings.project.books_dir)
    db = Database(base / "book.db")
    db.initialize()
    book = db.fetchone("SELECT * FROM books LIMIT 1")
    if book is None:
        db.close()
        raise ConfigError("the workspace has no book row", hint="re-run `shz init`")
    book_id = int(book["id"])
    db.abort_orphan_runs(book_id, settings.provider.timeout_s)
    if (
        book["source_format"] == "pdf"
        and book["import_quality"] is not None
        and float(book["import_quality"]) < 0.6
        and not options.accept_pdf_quality
    ):
        db.close()
        raise ConfigError(
            f"PDF import quality {float(book['import_quality']):.2f} is below 0.6",
            hint="fix artifacts/structure.yaml, or pass --accept-pdf-quality",
        )
    if (
        settings.glossary.mode == "auto"
        and settings.glossary.approval == "manual"
        and gate_blockers(db, book_id)
    ):
        blockers = ", ".join(gate_blockers(db, book_id)[:5])
        db.close()
        raise ConfigError(
            f"gate 1b: characters/places still need approval ({blockers})",
            hint="run `shz glossary approve` or switch glossary.approval to auto",
        )

    brief = load_approved_brief(base)
    warnings: list[str] = []
    if brief is None:
        brief = default_brief()
        warnings.append(
            "no approved brief; using the conservative built-in defaults (experimental run)"
        )
    terms = approved_terms(db, book_id) if settings.glossary.mode != "off" else []
    proper = load_proper_nouns(base) if settings.glossary.mode == "off" else NameList()
    anchors = [] if options.no_anchors else load_approved_anchors(base)

    mode, mode_reason = "standard", ""
    words = int(db.scalar("SELECT SUM(word_count) FROM chapters WHERE book_id = ?", (book_id,)) or 0)
    peak = is_peak()
    guard = BudgetGuard(
        cap_usd=options.max_usd if options.max_usd is not None else settings.budget.max_usd,
        warn_at_fraction=settings.budget.warn_at_fraction,
        allow_overrun=options.allow_overrun or not settings.budget.abort_on_exceed,
    )
    requested_mode = options.cost_mode or settings.pipeline.cost_mode
    if requested_mode == "auto":
        mode, mode_reason = select_cost_mode(words, profile, guard.cap_usd, peak=peak)
    elif requested_mode in {"economy", "standard", "quality"}:
        mode, mode_reason = requested_mode, f"explicit cost mode {requested_mode}"
    else:
        raise ConfigError(f"unknown cost mode: {requested_mode}")

    run_id = db.start_run(
        book_id=book_id,
        stage="translate",
        config_json=json.dumps(redacted_config(settings), sort_keys=True, ensure_ascii=False),
        artifacts_hash={
            "brief": brief_hash(brief),
            "glossary": _glossary_digest(terms),
            "anchors": sha256_text(json.dumps(anchors, sort_keys=True, ensure_ascii=False)),
        },
    )
    if options.refresh:
        db.execute("DELETE FROM cache")
    cache = ResponseCache(db, enabled=not options.no_cache, write_enabled=True)
    run = _Run(
        base=base,
        db=db,
        book=book,
        settings=settings,
        profile=profile,
        provider=provider,
        options=options,
        cache=cache,
        mode=mode,
        run_id=run_id,
        book_id=book_id,
        brief=brief,
        terms=terms,
        proper_nouns=proper,
        anchors=list(anchors),
        glossary_digest=_glossary_digest(terms),
        glossary_terms=[term.en for term in terms],
        lint_config=_lint_config(settings),
        guard=guard,
        started_at=utc_now_iso(),
        peak=peak,
        stats={
            "cost_mode_reason": mode_reason,
            "tariff": "peak" if peak else "offpeak",
            "experimental": load_approved_brief(base) is None,
        },
        warnings=warnings,
    )
    return run, TranslateResult(run_id=run_id, mode=mode, warnings=list(warnings))


def _finalize(run: _Run, result: TranslateResult, *, status: str) -> TranslateResult:
    db = run.db
    calls = db.calls_for_run(run.run_id)
    cost = round(sum(float(row["cost_usd"] or 0.0) for row in calls), 8)
    cached = sum(1 for row in calls if int(row["cached"]))
    selected_chapters = run.stats.get("chapter_ids")
    book_segments = db.segments(book_id=run.book_id)
    if isinstance(selected_chapters, list):
        wanted = {int(chapter_id) for chapter_id in selected_chapters}
        book_segments = [
            segment for segment in book_segments if int(segment["chapter_id"]) in wanted
        ]
    in_scope = [segment for segment in book_segments if str(segment["status"]) != SegmentStatus.SKIPPED]
    done: list[Any] = []
    for segment in in_scope:
        row = db.current_translation(str(segment["id"]))
        if row is None:
            continue
        text = str(row["fa_text"]).strip()
        if text and re.search(r"[\u0600-\u06FF]", text):
            done.append(segment)
    coverage = len(done) / max(1, len(in_scope))
    if isinstance(selected_chapters, list) and selected_chapters:
        marks = ",".join("?" for _ in selected_chapters)
        ids = tuple(int(chapter_id) for chapter_id in selected_chapters)
        error_issues = int(
            db.scalar(
                f"""
                SELECT COUNT(*) FROM issues i
                WHERE i.status = 'open' AND i.severity IN ('error','critical')
                  AND (
                    i.chapter_id IN ({marks})
                    OR i.segment_id IN (
                        SELECT id FROM segments WHERE chapter_id IN ({marks})
                    )
                  )
                """,
                (*ids, *ids),
            )
            or 0
        )
        failed = int(
            db.scalar(
                f"SELECT COUNT(*) FROM segments WHERE chapter_id IN ({marks}) AND status = 'failed'",
                ids,
            )
            or 0
        )
    else:
        error_issues = int(
            db.scalar(
                "SELECT COUNT(*) FROM issues WHERE status = 'open' "
                "AND severity IN ('error','critical')"
            )
            or 0
        )
        failed = int(
            db.scalar(
                "SELECT COUNT(*) FROM segments s JOIN chapters c ON c.id = s.chapter_id "
                "WHERE c.book_id = ? AND s.status = 'failed'",
                (run.book_id,),
            )
            or 0
        )
    stats = {
        **run.stats,
        "status": status,
        "chapters": len(db.chapters(run.book_id)),
        "calls": len(calls),
        "cached_calls": cached,
        "cache_hit_rate": round(cached / len(calls), 4) if calls else 1.0,
        "cost_usd": cost,
        "coverage": round(coverage, 4),
        "failed_segments": failed,
        "error_issues": error_issues,
        "tariff": "peak" if run.peak else "offpeak",
        "finished_at": utc_now_iso(),
    }
    db.update_run(run.run_id, stats=stats, cost_usd=cost)
    run_dir = run.base / "runs" / run.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_calls(run_dir, calls)
    run_row = db.get_run(run.run_id)
    from shahrzad.core.tokens import EN_TOKENS_PER_CHAR, FA_TOKENS_PER_CHAR
    from shahrzad.llm.promptlib import load as load_prompt

    prompt_versions = {
        prompt_id: load_prompt(prompt_id).version
        for prompt_id in (
            "p05_translator",
            "p06_fidelity_critic",
            "p07_literary_editor",
            "p08_reviser",
            "p09_ledger_curator",
        )
    }
    manifest = {
        "run_id": run.run_id,
        "book_slug": str(run.book["slug"]),
        "stage": "translate",
        "status": status,
        "started_at": run.started_at,
        "finished_at": utc_now_iso(),
        "config_snapshot": redacted_config(run.settings),
        "prompt_versions": prompt_versions,
        "window_plan_hash": run_row["window_plan_hash"] if run_row is not None else None,
        "artifacts_hash": _artifact_hashes(run.base),
        "models": {name: tier.model for name, tier in run.profile.tiers.items()},
        "stats": stats,
        "token_ratios": {
            "en_tokens_per_char": EN_TOKENS_PER_CHAR,
            "fa_tokens_per_char": FA_TOKENS_PER_CHAR,
        },
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    report = _report(run, stats, calls)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    db.transition_run(run.run_id, status)
    result.status = status
    result.warnings = list(run.warnings)
    result.cost_usd = cost
    result.calls = len(calls)
    result.cached_calls = cached
    result.cache_hit_rate = stats["cache_hit_rate"]
    result.coverage = coverage
    result.error_issues = error_issues
    result.segments_failed = failed
    result.report_path = run_dir / "report.md"
    return result


def _artifact_hashes(base: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("brief.yaml", "glossary.yaml", "anchors.yaml", "ledger.jsonl"):
        path = base / "artifacts" / name
        if path.is_file():
            result[name.split(".")[0]] = f"sha256:{sha256_text(path.read_text(encoding='utf-8'))}"
    return result


def _write_calls(run_dir: Path, calls: Sequence[Any]) -> None:
    with (run_dir / "calls.jsonl").open("w", encoding="utf-8") as handle:
        for row in calls:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def _report(run: _Run, stats: dict[str, Any], calls: Sequence[Any]) -> str:
    by_agent: dict[str, dict[str, float]] = {}
    for row in calls:
        entry = by_agent.setdefault(str(row["agent"]), {"calls": 0, "in": 0, "out": 0, "cost": 0.0})
        entry["calls"] += 1
        entry["in"] += int(row["tokens_in"] or 0)
        entry["out"] += int(row["tokens_out"] or 0)
        entry["cost"] += float(row["cost_usd"] or 0.0)
    words = int(
        run.db.scalar(
            "SELECT SUM(word_count) FROM chapters WHERE book_id = ?", (run.book_id,)
        )
        or 0
    )
    lines = [
        f"# Translation run {run.run_id}",
        "",
        f"- book: {run.book['slug']}",
        f"- status: {stats.get('status', 'completed')}",
        f"- started: {run.started_at}",
        f"- tariff: {stats.get('tariff')}",
        f"- cost mode: {run.mode} ({run.stats.get('cost_mode_reason', '')})",
        f"- coverage: {stats.get('coverage', 0):.1%}",
        f"- error issues: {stats.get('error_issues', 0)}",
        f"- cache hit rate: {stats.get('cache_hit_rate', 0):.1%}",
        f"- cost: ${stats.get('cost_usd', 0):.4f}",
        f"- cost per 1,000 words: ${(stats.get('cost_usd', 0.0) / max(1, words) * 1000):.4f}",
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
    lines.extend(["", "## Riskiest segments", "", "| segment | risk | status |", "|---|---:|---|"])
    rows = run.db.fetchall(
        """
        SELECT s.id, s.risk_score, s.status FROM segments s JOIN chapters c ON c.id = s.chapter_id
        WHERE c.book_id = ? AND s.risk_score > 0 ORDER BY s.risk_score DESC, s.id LIMIT 20
        """,
        (run.book_id,),
    )
    for row in rows:
        lines.append(f"| {row['id']} | {row['risk_score']} | {row['status']} |")
    if not rows:
        lines.append("| (none) | 0 | |")
    if stats.get("budget_paused"):
        lines.extend(
            [
                "",
                "## Budget pause",
                "",
                "The run paused at the budget cap. It is resumable: run `shz translate` again "
                "after raising `budget.max_usd` or pass `--allow-overrun`.",
            ]
        )
    return "\n".join(lines) + "\n"


def _warn_missing_anchors(run: _Run, chapters: Sequence[Any]) -> None:
    if run.options.no_anchors or run.anchors:
        return
    if any(int(chapter["idx"]) > 1 for chapter in chapters):
        message = (
            "no approved anchors; chapters 2+ rely on the brief alone. "
            "Run `shz translate --chapters 1` then `shz translate --calibrate` for gate 2."
        )
        if run.options.require_anchors:
            raise ConfigError(message, hint="approve anchors with `shz anchors approve`")
        if message not in run.warnings:
            run.warnings.append(message)


def run_translate(
    *,
    root: Path,
    slug: str | None,
    settings: Settings,
    profile: ModelProfile,
    provider: Provider,
    options: TranslateOptions | None = None,
    progress: ProgressCallback | None = None,
) -> TranslateResult:
    options = options or TranslateOptions()
    run: _Run | None = None
    try:
        run, result = _open_run(
            root=root,
            slug=slug,
            settings=settings,
            profile=profile,
            provider=provider,
            options=options,
        )
        run.progress = progress
        chapters = _select_chapters(run)
        _warn_missing_anchors(run, chapters)
        result.warnings = list(run.warnings)
        if options.calibrate:
            from .anchors_pass import propose_anchors

            proposed = propose_anchors(
                root=root, slug=slug, db=run.db, book_id=run.book_id
            )
            result.warnings.append(
                f"anchors draft written to {proposed.path}; approve with `shz anchors approve`"
            )
            return _finalize(run, result, status=str(RunStatus.COMPLETED))
        all_windows: list[Window] = []
        for chapter in chapters:
            chapter_segments = run.db.segments(chapter_id=int(chapter["id"]))
            all_windows.extend(
                plan_windows(
                    chapter_segments,
                    chapter_id=int(chapter["id"]),
                    chapter_idx=int(chapter["idx"]),
                    max_paragraphs=settings.pipeline.window.max_paragraphs,
                    max_source_tokens=settings.pipeline.window.max_source_tokens,
                    overlap_paragraphs=settings.pipeline.window.overlap_paragraphs,
                )
            )
        digest = plan_hash(all_windows)
        run.db.execute(
            "UPDATE runs SET window_plan_hash = ? WHERE id = ?", (digest, run.run_id)
        )
        scope = _scope_label(chapters)
        run.stats["scope"] = scope
        run.stats["window_plan_hash"] = digest
        previous = run.db.fetchone(
            "SELECT * FROM runs WHERE book_id = ? AND stage = 'translate' AND id != ? "
            "ORDER BY started_at DESC, rowid DESC LIMIT 1",
            (run.book_id, run.run_id),
        )
        if (
            previous is not None
            and previous["window_plan_hash"]
            and previous["window_plan_hash"] != digest
            and not options.replan
            and not options.force
        ):
            try:
                previous_stats = json.loads(previous["stats_json"] or "{}")
            except json.JSONDecodeError:
                previous_stats = {}
            if previous_stats.get("scope") == scope:
                if options.replan:
                    options.force = True
                    run.warnings.append(
                        "window plan changed; --replan is retranslating the affected windows "
                        "as new versions (history is append-only)"
                    )
                else:
                    run.db.transition_run(run.run_id, RunStatus.FAILED)
                    raise ConfigError(
                        "window plan changed since the previous translation run",
                        hint="run `shz translate --replan` to archive the affected plan and continue",
                    )
        if options.dry_run:
            words = sum(int(chapter["word_count"] or 0) for chapter in chapters)
            estimate = estimate_book(words, run.mode, profile)
            run.db.transition_run(run.run_id, RunStatus.COMPLETED)
            result.dry_run = True
            result.estimate = {
                "mode": run.mode,
                "words": words,
                "calls": estimate.calls,
                "peak_usd": estimate.peak_usd,
                "offpeak_usd": estimate.offpeak_usd,
            }
            result.coverage = 0.0
            return result

        progress_windows = all_windows
        if options.windows:
            allowed_windows = set(options.windows)
            progress_windows = [
                window for window in all_windows if window.id in allowed_windows
            ]
        _progress_start(run, chapters=len(chapters), windows=len(progress_windows))

        result.chapters = len(chapters)
        run.stats["chapter_ids"] = [int(chapter["id"]) for chapter in chapters]
        if options.parallel_chapters > 1 and len(chapters) > 1:
            from shahrzad.core.logging import get_logger

            get_logger(__name__).warning(
                "parallel chapters: %d workers; ledger chaining is disabled and coherence "
                "may be slightly lower than reading-order translation",
                options.parallel_chapters,
            )
            run.warnings.append(
                "parallel-chapter mode: ledger chaining is disabled; coherence may be "
                "slightly lower than reading-order translation"
            )
            with ThreadPoolExecutor(max_workers=options.parallel_chapters) as pool:
                futures = {
                    pool.submit(
                        _process_chapter,
                        run,
                        chapter,
                        position=index,
                        total=len(chapters),
                    ): chapter
                    for index, chapter in enumerate(chapters, start=1)
                }
                for future in as_completed(futures):
                    chapter = futures[future]
                    try:
                        stats = future.result()
                    except BudgetPaused:
                        run.paused = True
                        run.stop = True
                        continue
                    except Exception as exc:  # noqa: BLE001 - per-item failure must not abort
                        _record_chapter_failure(run, chapter, exc)
                        continue
                    result.segments_translated += int(stats.get("segments", 0))
                    result.windows += int(stats.get("windows", 0))
        else:
            for index, chapter in enumerate(chapters, start=1):
                if run.stop or run.paused:
                    break
                try:
                    stats = _process_chapter(
                        run, chapter, position=index, total=len(chapters)
                    )
                except BudgetPaused:
                    run.paused = True
                    break
                except Exception as exc:  # noqa: BLE001 - per-item failure must not abort
                    _record_chapter_failure(run, chapter, exc)
                    continue
                result.segments_translated += int(stats.get("segments", 0))
                result.windows += int(stats.get("windows", 0))

        status = RunStatus.PAUSED if run.paused else RunStatus.COMPLETED
        if run.paused:
            run.progress_status = "paused"
            run.stats["budget_paused"] = True
        result = _finalize(run, result, status=str(status))
        result.paused = run.paused
        if run.paused:
            raise BudgetPaused(
                "translation paused at the budget cap; state is saved",
                hint="resume with `shz translate` after raising budget.max_usd or pass --allow-overrun",
            )
        return result
    except BudgetPaused:
        if run is not None:
            status = RunStatus.PAUSED
            run.progress_status = "paused"
            run.stats["budget_paused"] = True
            previous = run.db.get_run(run.run_id)
            if previous is not None and previous["status"] == RunStatus.RUNNING:
                _finalize(run, TranslateResult(run_id=run.run_id, mode=run.mode), status=str(status))
        raise
    except Exception:
        if run is not None:
            run.progress_status = "failed"
            current = run.db.get_run(run.run_id)
            if current is not None and current["status"] == RunStatus.RUNNING:
                _finalize(
                    run,
                    TranslateResult(run_id=run.run_id, mode=run.mode),
                    status=str(RunStatus.FAILED),
                )
        raise
    finally:
        if run is not None:
            _progress_finish(run)
            run.db.close()


def _record_chapter_failure(run: _Run, chapter: Any, exc: Exception) -> None:
    run.db.upsert_issue(
        stage="translate",
        code="CHAPTER_FAILED",
        dedupe_key=f"ch:{chapter['idx']}:CHAPTER_FAILED",
        severity=Severity.ERROR,
        message=f"chapter {chapter['idx']} failed: {exc}",
        chapter_id=int(chapter["id"]),
    )
    run.warnings.append(f"chapter {chapter['idx']} failed: {exc}")
