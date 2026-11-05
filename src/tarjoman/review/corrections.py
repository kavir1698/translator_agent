"""Apply `review/corrections.jsonl` as human translation versions (plan 6.9)."""

from __future__ import annotations

import csv
import json
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tarjoman.core.config import Settings
from tarjoman.core.db import _SEGMENT_TRANSITIONS, Database
from tarjoman.core.errors import ConfigError, RuntimeFailure
from tarjoman.core.models import SegmentStatus
from tarjoman.core.paths import find_book_dir
from tarjoman.persian.markup import NormalizedTranslation, normalize_translation
from tarjoman.pipeline.qa_pass import (
    FLAGS_COLUMNS,
    QaOptions,
    QaResult,
    build_flag_row,
    run_qa,
)

_NEEDS_WORK_MESSAGE = "reviewer marked this segment as needing work"
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value: str) -> str:
    return f"'{value}" if value.startswith(_CSV_FORMULA_PREFIXES) else value

_GRAPH: dict[str, set[str]] = {
    str(source): {str(target) for target in targets}
    for source, targets in _SEGMENT_TRANSITIONS.items()
}


@dataclass
class ApplyResult:
    applied: int
    needs_work: int
    flags_updated: int
    new_versions: int
    resolved: int = 0


@dataclass
class _Correction:
    segment_id: str
    action: str
    note: str
    reviewer: str
    normalized: NormalizedTranslation | None = None
    base_version: int | None = None


def _nodes(segment: Any) -> list[dict[str, Any]]:
    raw = segment["nodes_json"]
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
    return list(raw or [])


def _glossary_terms(current: Any) -> list[str]:
    if current is None:
        return []
    try:
        data = json.loads(current["glossary_terms_json"] or "[]")
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def _parse(
    *,
    db: Database,
    settings: Settings,
    corrections_path: Path,
    default_reviewer: str,
    force: bool,
) -> list[_Correction]:
    try:
        text = corrections_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"cannot read corrections file: {corrections_path}",
            hint="pass the JSONL file downloaded from review/report.html",
        ) from exc

    corrections: list[_Correction] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeFailure(
                f"{corrections_path}:{line_number}: invalid JSON ({exc.msg})",
                hint="each line must be one JSON object; re-download corrections.jsonl",
            ) from exc
        if not isinstance(item, dict):
            raise RuntimeFailure(
                f"{corrections_path}:{line_number}: correction must be a JSON object",
                hint="each line must be one JSON object",
            )
        segment_id = item.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise RuntimeFailure(
                f"{corrections_path}:{line_number}: missing segment_id",
                hint="copy segment ids from review/report.html (for example c003.s0042)",
            )
        segment_id = segment_id.strip()
        segment = db.get_segment(segment_id)
        if segment is None:
            raise RuntimeFailure(
                f"{corrections_path}:{line_number}: segment {segment_id!r} does not exist",
                hint="check the id; run `tj review build` to regenerate valid segment ids",
            )
        action = item.get("action")
        if action not in {"accept", "needs_work"}:
            raise ConfigError(
                f"{corrections_path}:{line_number}: unknown action {action!r} for {segment_id}",
                hint="allowed actions: accept, needs_work",
            )
        note = item.get("note", item.get("notes", ""))
        note_text = "" if note is None else str(note)
        reviewer = str(item.get("reviewer") or default_reviewer).strip() or default_reviewer
        base_version: int | None = None
        raw_version = item.get("translation_version")
        if raw_version is not None:
            try:
                base_version = int(raw_version)
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    f"{corrections_path}:{line_number}: translation_version must be an integer",
                    hint="re-download corrections.jsonl from review/report.html",
                ) from exc
            current = db.current_translation(segment_id)
            if not force and (
                current is None or int(current["version"]) != base_version
            ):
                current_version = None if current is None else int(current["version"])
                raise RuntimeFailure(
                    f"{corrections_path}:{line_number}: segment {segment_id} changed since "
                    f"the report was built (reviewed version {base_version}, current "
                    f"version {current_version})",
                    hint="run `tj review build`, review the current text, and retry; "
                    "pass --force only if overwriting the newer version is intentional",
                )
        normalized: NormalizedTranslation | None = None
        if action == "accept":
            fa_markup = item.get("fa_markup")
            if not isinstance(fa_markup, str) or not fa_markup.strip():
                raise ConfigError(
                    f"{corrections_path}:{line_number}: action 'accept' for {segment_id} "
                    "requires a non-empty fa_markup",
                    hint="copy the textarea value from review/report.html",
                )
            normalized = normalize_translation(
                fa_markup,
                _nodes(segment),
                digits=settings.persian.digits,
                keep_diacritics=settings.persian.keep_diacritics,
                ellipsis=settings.persian.ellipsis,
            )
            if normalized.problems:
                codes = ", ".join(sorted({problem.code for problem in normalized.problems}))
                details = "; ".join(
                    f"{problem.code}: {problem.message}" for problem in normalized.problems[:4]
                )
                raise RuntimeFailure(
                    f"{corrections_path}:{line_number}: segment {segment_id} failed markup "
                    f"validation ({codes}): {details}",
                    hint="keep every existing sentinel id unchanged; do not invent, duplicate "
                    "or remove inline markup",
                )
        corrections.append(
            _Correction(
                segment_id=segment_id,
                action=str(action),
                note=note_text,
                reviewer=reviewer,
                normalized=normalized,
                base_version=base_version,
            )
        )
    return corrections


def _resolve_open_issues(
    db: Database, segment_id: str, reviewer: str, note: str, *, stage: str | None = None
) -> int:
    count = 0
    for row in db.issues_for(segment_id=segment_id, status="open"):
        if stage is not None and str(row["stage"]) != stage:
            continue
        db.set_issue_status(
            int(row["id"]),
            "resolved",
            resolved_by=reviewer,
            resolution=note or "review correction accepted",
        )
        count += 1
    return count


def _transition_path(current: str, target: str) -> list[str]:
    if current == target:
        return []
    parents: dict[str, str | None] = {current: None}
    queue: deque[str] = deque([current])
    while queue:
        node = queue.popleft()
        for following in sorted(_GRAPH.get(node, set())):
            if following in parents:
                continue
            parents[following] = node
            if following == target:
                path: list[str] = []
                cursor: str | None = following
                while cursor is not None and cursor != current:
                    path.append(cursor)
                    cursor = parents[cursor]
                return list(reversed(path))
            queue.append(following)
    return []


def _ensure_status(db: Database, segment_id: str, target: str) -> bool:
    row = db.get_segment(segment_id)
    if row is None:
        raise RuntimeFailure(f"segment {segment_id} does not exist")
    current = str(row["status"])
    if current == target:
        return True
    path = _transition_path(current, target)
    if not path:
        raise RuntimeFailure(
            f"segment {segment_id}: cannot transition from {current} to {target}",
            hint="refresh the workspace with `tj qa` and retry",
        )
    for step in path:
        db.transition_segment(segment_id, step)
    return True


def _open_blocking_issues(db: Database, segment_id: str) -> int:
    return sum(
        1
        for issue in db.issues_for(segment_id=segment_id, status="open")
        if str(issue["stage"]) == "qa" and str(issue["severity"]) in {"error", "critical"}
    )


def _flag_extra(
    db: Database,
    correction: _Correction,
    qa_rows: dict[str, dict[str, Any]],
    chapter_idx: dict[int, int],
) -> dict[str, Any]:
    existing = qa_rows.get(correction.segment_id)
    if existing is not None:
        row = dict(existing)
    else:
        segment = db.get_segment(correction.segment_id)
        if segment is None:
            raise RuntimeFailure(f"segment {correction.segment_id} does not exist")
        issues = db.issues_for(segment_id=correction.segment_id, status="open")
        translation = db.current_translation(correction.segment_id)
        chapter_id = int(segment["chapter_id"])
        row = build_flag_row(
            segment,
            int(segment["risk_score"]),
            issues,
            chapter_idx=chapter_idx.get(chapter_id, chapter_id),
            fa_text=str(translation["fa_text"]) if translation else "",
        )
    row["reviewed"] = "true"
    row["reviewer_note"] = _csv_safe(correction.note)
    return row


def _update_flags(
    flags_path: Path,
    corrections: list[_Correction],
    db: Database,
    qa: QaResult,
    chapter_idx: dict[int, int],
) -> int:
    rows: list[dict[str, Any]] = []
    if flags_path.is_file():
        with flags_path.open("r", encoding="utf-8", newline="") as handle:
            rows = [dict(row) for row in csv.DictReader(handle)]
    by_segment = {str(row.get("segment_id", "")): row for row in rows}
    qa_rows = {str(row["segment_id"]): row for row in qa.rows}
    updated: set[str] = set()
    for correction in corrections:
        if correction.segment_id in updated:
            continue
        row = by_segment.get(correction.segment_id)
        if row is None:
            row = _flag_extra(db, correction, qa_rows, chapter_idx)
            rows.append(row)
            by_segment[correction.segment_id] = row
        row["reviewed"] = "true"
        row["reviewer_note"] = _csv_safe(correction.note)
        updated.add(correction.segment_id)

    flags_path.parent.mkdir(parents=True, exist_ok=True)
    temp = flags_path.with_name(f"{flags_path.name}.tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FLAGS_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in FLAGS_COLUMNS})
    temp.replace(flags_path)
    return len(updated)


def apply_corrections(
    *,
    root: Path,
    slug: str | None,
    settings: Settings,
    corrections_path: Path,
    dry_run: bool = False,
    reviewer: str | None = None,
    force: bool = False,
) -> ApplyResult:
    if not corrections_path.is_file():
        raise ConfigError(
            f"corrections file not found: {corrections_path}",
            hint="download corrections.jsonl from the review report first",
        )
    base = find_book_dir(root, slug, settings.project.books_dir)
    db = Database(base / "book.db")
    db.initialize()
    try:
        book = db.fetchone("SELECT * FROM books LIMIT 1")
        if book is None:
            raise ConfigError("the workspace has no book row", hint="re-run `tj init`")
        default_reviewer = (reviewer or "editor").strip() or "editor"
        corrections = _parse(
            db=db,
            settings=settings,
            corrections_path=corrections_path,
            default_reviewer=default_reviewer,
            force=force,
        )
        accepted = [item for item in corrections if item.action == "accept"]
        needs_work = [item for item in corrections if item.action == "needs_work"]
        if dry_run:
            return ApplyResult(
                applied=len(accepted),
                needs_work=len(needs_work),
                flags_updated=0,
                new_versions=0,
            )
        if not corrections:
            return ApplyResult(
                applied=0, needs_work=0, flags_updated=0, new_versions=0
            )

        new_versions = 0
        for correction in accepted:
            if correction.normalized is None:
                continue
            current = db.current_translation(correction.segment_id)
            db.add_translation(
                segment_id=correction.segment_id,
                pass_kind="human",
                fa_text=correction.normalized.fa_text,
                fa_markup=correction.normalized.fa_markup,
                model="human",
                notes={
                    "correction_note": correction.note,
                    "reviewer": correction.reviewer,
                },
                glossary_hash=current["glossary_hash"] if current is not None else None,
                glossary_terms=_glossary_terms(current),
                make_current=True,
            )
            new_versions += 1

        # Attribute the lint resolutions to the reviewer before qa re-opens any that still fail.
        resolved = 0
        for correction in accepted:
            resolved += _resolve_open_issues(
                db, correction.segment_id, correction.reviewer, correction.note, stage="qa"
            )

        qa = run_qa(
            root=root,
            slug=slug,
            settings=settings,
            options=QaOptions(preserve_flags=True),
        )

        # qa resolves issue keys it does not reproduce, so re-open needs_work afterwards.
        for correction in needs_work:
            segment = db.get_segment(correction.segment_id)
            if segment is None:
                raise RuntimeFailure(f"segment {correction.segment_id} does not exist")
            db.upsert_issue(
                stage="qa",
                code="REVIEW_NEEDS_WORK",
                dedupe_key=f"seg:{correction.segment_id}:REVIEW_NEEDS_WORK",
                severity="warn",
                message=correction.note or _NEEDS_WORK_MESSAGE,
                segment_id=correction.segment_id,
                chapter_id=int(segment["chapter_id"]),
            )
            _ensure_status(db, correction.segment_id, SegmentStatus.FLAGGED)

        chapter_idx: dict[int, int] = {}
        book_id = int(book["id"])
        for chapter in db.chapters(book_id):
            chapter_idx[int(chapter["id"])] = int(chapter["idx"])

        for correction in accepted:
            if _open_blocking_issues(db, correction.segment_id):
                _ensure_status(db, correction.segment_id, SegmentStatus.FLAGGED)
                continue
            resolved += _resolve_open_issues(
                db, correction.segment_id, correction.reviewer, correction.note
            )
            _ensure_status(db, correction.segment_id, SegmentStatus.DONE)

        flags_updated = _update_flags(
            base / "review" / "flags.csv",
            corrections,
            db,
            qa,
            chapter_idx,
        )
        return ApplyResult(
            applied=len(accepted),
            needs_work=len(needs_work),
            flags_updated=flags_updated,
            new_versions=new_versions,
            resolved=resolved,
        )
    finally:
        db.close()
