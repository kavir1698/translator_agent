"""Single-file HTML review report (plan 6.9)."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from tarjoman.core.atomic import atomic_write_text
from tarjoman.core.config import Settings
from tarjoman.core.db import Database
from tarjoman.core.errors import ConfigError
from tarjoman.core.paths import find_book_dir
from tarjoman.persian.lint import GlossaryTerm, term_in_text
from tarjoman.pipeline.glossary_ops import approved_terms
from tarjoman.pipeline.qa_pass import QaOptions, QaResult, run_qa

_SENTINEL = re.compile(r"⟨/?\d+⟩|⟦/?\d+⟧|\[\[#\d+\]\]|\[\[/\d+\]\]")
_TEMPLATE_DIR = Path(__file__).parent
_TEMPLATE_NAME = "report_template.html"


@dataclass
class ReviewBuildOptions:
    chapter: int | None = None
    out: Path | None = None


@dataclass
class ReviewBuildResult:
    path: Path
    rows: int
    coverage: float
    adherence: float
    issues: int
    errors: int
    criticals: int
    cost_usd: float
    prompt_versions: list[str]


@dataclass
class _ReportRow:
    segment_id: str
    chapter: int
    risk: int
    codes: str
    severities: str
    flagged: bool
    source_html: str
    fa_markup: str
    preview: Markup
    issues: list[dict[str, str]]
    notes: dict[str, str]
    terms: list[dict[str, str]]
    translation_version: int | None


def _text(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _loads(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw or "null")
        except json.JSONDecodeError:
            return None
    return raw


def _notes(raw: Any) -> dict[str, str]:
    data = _loads(raw)
    if not isinstance(data, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if value is not None and str(value) != ""
    }


def _preview(markup: str) -> Markup:
    """Escape the translation and turn every sentinel into a visible chip."""
    parts: list[str] = []
    position = 0
    for match in _SENTINEL.finditer(markup):
        parts.append(html.escape(markup[position : match.start()]))
        parts.append(f'<span class="sentinel">{html.escape(match.group(0))}</span>')
        position = match.end()
    parts.append(html.escape(markup[position:]))
    return Markup("".join(parts))


def _issue_view(row: Any) -> dict[str, str]:
    return {
        "code": _text(row["code"]),
        "severity": _text(row["severity"]),
        "message": _text(row["message"]),
        "quote_source": _text(row["quote_source"]),
        "quote_target": _text(row["quote_target"]),
    }


def _adherence(
    segments: list[Any],
    translations: dict[str, Any],
    terms: list[GlossaryTerm],
) -> tuple[float, int, int, dict[str, list[dict[str, str]]]]:
    """Per segment-term occurrence: approved Persian form or alias must appear."""
    occurrences = 0
    matched = 0
    by_segment: dict[str, list[dict[str, str]]] = {}
    for segment in segments:
        segment_id = str(segment["id"])
        translation = translations.get(segment_id)
        if translation is None:
            continue
        source = str(segment["source_text"])
        target = str(translation["fa_text"])
        found: list[dict[str, str]] = []
        for term in terms:
            source_forms = (term.en, *term.aliases_en)
            if not any(term_in_text(form, source) for form in source_forms if form):
                continue
            found.append({"en": term.en, "fa": term.fa, "kind": term.kind})
            occurrences += 1
            target_forms = (term.fa, *term.aliases_fa)
            if any(term_in_text(form, target) for form in target_forms if form):
                matched += 1
        if found:
            by_segment[segment_id] = found
    if occurrences == 0:
        return 1.0, 0, 0, by_segment
    return matched / occurrences, matched, occurrences, by_segment


def _severity_counts(db: Database, book_id: int, chapter: int | None) -> dict[str, int]:
    counts = {"critical": 0, "error": 0, "warn": 0, "info": 0}
    rows = db.fetchall(
        """
        SELECT i.severity, c.idx AS chapter_idx
        FROM issues i
        LEFT JOIN segments s ON s.id = i.segment_id
        LEFT JOIN chapters c ON c.id = COALESCE(i.chapter_id, s.chapter_id)
        WHERE i.status = 'open'
          AND (c.book_id = ?
               OR (i.chapter_id IS NULL AND i.segment_id IS NULL))
        """,
        (book_id,),
    )
    for row in rows:
        index = row["chapter_idx"]
        if chapter is not None and (index is None or int(index) != chapter):
            continue
        severity = str(row["severity"])
        if severity in counts:
            counts[severity] += 1
    return counts


def _cost_and_prompts(db: Database, book_id: int) -> tuple[float, list[str]]:
    cost = float(
        db.scalar(
            """
            SELECT COALESCE(SUM(t.cost_usd), 0) FROM translations t
            JOIN segments s ON s.id = t.segment_id
            JOIN chapters c ON c.id = s.chapter_id
            WHERE c.book_id = ?
            """,
            (book_id,),
        )
        or 0.0
    )
    hashes = db.fetchall(
        """
        SELECT DISTINCT t.prompt_hash FROM translations t
        JOIN segments s ON s.id = t.segment_id
        JOIN chapters c ON c.id = s.chapter_id
        WHERE c.book_id = ? AND t.prompt_hash IS NOT NULL AND t.prompt_hash != ''
        ORDER BY t.prompt_hash
        """,
        (book_id,),
    )
    versions = sorted({str(row["prompt_hash"])[:8] for row in hashes})
    return cost, versions


def _report_row(
    segment: Any,
    chapter_idx: int,
    qa_row: dict[str, Any] | None,
    translation: Any,
    issues: list[Any],
    terms: list[dict[str, str]],
) -> _ReportRow:
    segment_id = str(segment["id"])
    if qa_row is not None:
        risk = int(qa_row["risk_score"])
        codes = str(qa_row["codes"])
        severities = str(qa_row["severities"])
    else:
        risk = int(segment["risk_score"])
        codes = "|".join(sorted({str(issue["code"]) for issue in issues}))
        severities = "|".join(sorted({str(issue["severity"]) for issue in issues}))
    markup = str(translation["fa_markup"]) if translation is not None else ""
    return _ReportRow(
        segment_id=segment_id,
        chapter=chapter_idx,
        risk=risk,
        codes=codes,
        severities=severities,
        flagged=bool(codes),
        source_html=str(segment["source_html"]),
        fa_markup=markup,
        preview=_preview(markup),
        issues=[_issue_view(issue) for issue in issues],
        notes=_notes(translation["notes_json"]) if translation is not None else {},
        terms=terms,
        translation_version=int(translation["version"]) if translation is not None else None,
    )


def build_report(
    *,
    root: Path,
    slug: str | None,
    settings: Settings,
    options: ReviewBuildOptions | None = None,
) -> ReviewBuildResult:
    options = options or ReviewBuildOptions()
    base = find_book_dir(root, slug, settings.project.books_dir)
    qa: QaResult = run_qa(
        root=root,
        slug=slug,
        settings=settings,
        options=QaOptions(chapter=options.chapter, preserve_flags=True),
    )
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
        terms = approved_terms(db, book_id)

        chapter_by_id = {int(chapter["id"]): int(chapter["idx"]) for chapter in chapters}
        selected_ids = set(chapter_by_id)
        segments = [
            segment
            for segment in db.segments(book_id=book_id)
            if int(segment["chapter_id"]) in selected_ids
        ]
        translations = {
            str(row["segment_id"]): row
            for row in db.fetchall(
                """
                SELECT t.* FROM translations t
                JOIN segments s ON s.id = t.segment_id
                JOIN chapters c ON c.id = s.chapter_id
                WHERE c.book_id = ? AND t.is_current = 1
                """,
                (book_id,),
            )
        }
        issues_by_segment: dict[str, list[Any]] = {}
        for issue in db.fetchall(
            """
            SELECT i.* FROM issues i
            JOIN segments s ON s.id = i.segment_id
            JOIN chapters c ON c.id = s.chapter_id
            WHERE c.book_id = ? AND i.status = 'open'
            ORDER BY i.id
            """,
            (book_id,),
        ):
            issues_by_segment.setdefault(str(issue["segment_id"]), []).append(issue)

        adherence, matched, occurrences, terms_by_segment = _adherence(
            segments, translations, terms
        )
        cost, prompt_versions = _cost_and_prompts(db, book_id)
        qa_rows = {str(row["segment_id"]): row for row in qa.rows}
        rows = [
            _report_row(
                segment,
                chapter_by_id.get(int(segment["chapter_id"]), int(segment["chapter_id"])),
                qa_rows.get(str(segment["id"])),
                translations.get(str(segment["id"])),
                issues_by_segment.get(str(segment["id"]), []),
                terms_by_segment.get(str(segment["id"]), []),
            )
            for segment in segments
        ]
        for row in rows:
            row.flagged = row.codes != "" or row.risk >= settings.review.flag_threshold
        all_codes = sorted({code for row in rows for code in row.codes.split("|") if code})

        model_config = settings.provider.models
        if Path(model_config).is_absolute():
            model_config = Path(model_config).name

        environment = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=select_autoescape(enabled_extensions=("html", "xml")),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        template = environment.get_template(_TEMPLATE_NAME)
        document = template.render(
            book_title=_text(book["title_fa"] or book["title_en"]),
            coverage_pct=f"{qa.coverage:.1%}",
            adherence_pct=f"{adherence:.1%}",
            matched_terms=matched,
            term_occurrences=occurrences,
            severity_counts=_severity_counts(db, book_id, options.chapter),
            cost_usd=f"{cost:.4f}",
            model_profile=settings.provider.profile,
            model_config=model_config,
            prompt_versions=prompt_versions,
            all_codes=all_codes,
            rows=rows,
        )

        out_path = options.out if options.out is not None else base / "review" / "report.html"
        if not out_path.is_absolute():
            out_path = base / out_path
        atomic_write_text(out_path, document)
        return ReviewBuildResult(
            path=out_path,
            rows=len(rows),
            coverage=qa.coverage,
            adherence=adherence,
            issues=qa.issues,
            errors=qa.errors,
            criticals=qa.criticals,
            cost_usd=cost,
            prompt_versions=prompt_versions,
        )
    finally:
        db.close()
