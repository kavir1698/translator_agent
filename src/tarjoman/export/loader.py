"""Load the approved book state into the export model (read-only)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tarjoman.core.config import Settings
from tarjoman.core.db import Database
from tarjoman.core.errors import ConfigError, RuntimeFailure
from tarjoman.core.ids import utc_now_iso
from tarjoman.core.paths import find_book_dir

from .models import ExportBook, ExportChapter, ExportSegment

_PERSIAN_LETTER = re.compile(r"[\u0600-\u06FF]")
_EXAMPLES = 5


def _json_dict(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _nodes(raw: object) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [node for node in raw if isinstance(node, dict)]
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [node for node in value if isinstance(node, dict)] if isinstance(value, list) else []


def _issue_dict(row: Any) -> dict[str, Any]:
    return {
        "code": str(row["code"]),
        "severity": str(row["severity"]),
        "message": str(row["message"]),
        "quote_source": str(row["quote_source"] or ""),
        "quote_target": str(row["quote_target"] or ""),
        "status": str(row["status"]),
    }


def _has_persian(text: str) -> bool:
    return bool(_PERSIAN_LETTER.search(text))


def _normalize_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise ConfigError("--generated-at must not be empty")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ConfigError(
            f"invalid --generated-at timestamp: {value!r}",
            hint="use an ISO-8601 value such as 2026-11-05T18:30:00Z",
        ) from exc
    if parsed.tzinfo is None:
        raise ConfigError(
            f"--generated-at has no timezone: {value!r}",
            hint="append Z or an explicit offset, e.g. 2026-11-05T18:30:00Z",
        )
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_generated_at(db: Database, book_id: int) -> str:
    row = db.fetchone(
        """
        SELECT MAX(t.created_at) FROM translations t
        JOIN segments s ON s.id = t.segment_id
        JOIN chapters c ON c.id = s.chapter_id
        WHERE c.book_id = ?
        """,
        (book_id,),
    )
    raw = str(row[0]) if row is not None and row[0] else None
    normalized = _normalize_timestamp(raw)
    return normalized or utc_now_iso()


def _source_metadata(base: Path) -> dict[str, Any]:
    path = base / "source" / "metadata.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _resolve_source_path(base: Path, raw: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(base.resolve())
    except ValueError:
        return candidate.resolve()
    return resolved


def _load_segment(
    row: Any, translation: Any, issues: list[Any]
) -> ExportSegment:
    return ExportSegment(
        id=str(row["id"]),
        idx=int(row["idx"]),
        kind=str(row["kind"]),
        source_text=str(row["source_text"]),
        source_html=str(row["source_html"]),
        nodes=_nodes(row["nodes_json"]),
        fa_text=str(translation["fa_text"]) if translation is not None else "",
        fa_markup=str(translation["fa_markup"]) if translation is not None else "",
        notes=_json_dict(translation["notes_json"]) if translation is not None else {},
        issues=[_issue_dict(item) for item in issues],
    )


def _build_chapters(db: Database, book_id: int) -> tuple[list[ExportChapter], list[str]]:
    chapters = db.chapters(book_id)
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
    issues: dict[str, list[Any]] = {}
    for issue in db.fetchall(
        """
        SELECT i.* FROM issues i
        JOIN segments s ON s.id = i.segment_id
        JOIN chapters c ON c.id = s.chapter_id
        WHERE c.book_id = ?
        ORDER BY i.id
        """,
        (book_id,),
    ):
        issues.setdefault(str(issue["segment_id"]), []).append(issue)
    segments = db.segments(book_id=book_id)
    by_chapter: dict[int, list[Any]] = {}
    for segment_row in segments:
        by_chapter.setdefault(int(segment_row["chapter_id"]), []).append(segment_row)

    result: list[ExportChapter] = []
    missing: list[str] = []
    for chapter in chapters:
        chapter_row = ExportChapter(
            idx=int(chapter["idx"]),
            kind=str(chapter["kind"]),
            title_en=str(chapter["title_en"]) if chapter["title_en"] else None,
            title_fa=str(chapter["title_fa"]) if chapter["title_fa"] else None,
        )
        for row in by_chapter.get(int(chapter["id"]), []):
            if str(row["status"]) == "skipped":
                continue
            loaded = _load_segment(
                row,
                translations.get(str(row["id"])),
                issues.get(str(row["id"]), []),
            )
            chapter_row.segments.append(loaded)
            if chapter_row.kind == "body" and (
                str(row["status"]) != "done" or not _has_persian(loaded.fa_text.strip())
            ):
                missing.append(loaded.id)
        result.append(chapter_row)
    return result, missing


def _count_open_errors(db: Database) -> int:
    value = db.scalar(
        "SELECT COUNT(*) FROM issues WHERE status = 'open' AND severity IN ('error','critical')"
    )
    return int(value or 0)


def load_export_book(
    *,
    root: Path,
    slug: str | None,
    settings: Settings,
    allow_incomplete: bool = False,
    generated_at: str | None = None,
) -> ExportBook:
    """Load one workspace without writing to the database or the filesystem."""
    base = find_book_dir(root, slug, settings.project.books_dir)
    db = Database(base / "book.db")
    db.connect()
    try:
        book = db.fetchone("SELECT * FROM books LIMIT 1")
        if book is None:
            raise ConfigError("the workspace has no book row", hint="re-run `tj init`")
        book_id = int(book["id"])
        chapters, missing = _build_chapters(db, book_id)
        open_errors = _count_open_errors(db)

        body_segments = sum(
            len(chapter.segments) for chapter in chapters if chapter.kind == "body"
        )
        coverage = 1.0 if body_segments == 0 else (body_segments - len(missing)) / body_segments
        minimum = float(settings.review.min_coverage_for_export)
        incomplete = bool(missing) or coverage < minimum

        if (incomplete or open_errors) and not allow_incomplete:
            example_ids = ", ".join(missing[:_EXAMPLES]) if missing else "none"
            raise RuntimeFailure(
                "export blocked: "
                f"{len(missing)} of {body_segments} body segment(s) are not done or lack a "
                f"current Persian translation, coverage is {coverage:.1%} "
                f"(minimum {minimum:.1%}), and "
                f"{open_errors} open error/critical issue(s) remain "
                f"(examples: {example_ids})",
                hint="run `tj translate`, resolve the open issues, or pass `--allow-incomplete`",
            )

        source_meta = _source_metadata(base)
        metadata = {**_json_dict(book["metadata_json"]), **source_meta}
        cover_value = source_meta.get("cover")
        cover = str(cover_value) if cover_value else None
        title_fa = str(book["title_fa"]) if book["title_fa"] else None
        if not title_fa:
            meta_title = metadata.get("title_fa") or settings.book.title_fa
            title_fa = str(meta_title) if meta_title else None

        timestamp = (
            _normalize_timestamp(generated_at)
            if generated_at is not None
            else _default_generated_at(db, book_id)
        )
        assert timestamp is not None

        return ExportBook(
            slug=str(book["slug"]),
            title_en=str(book["title_en"]),
            title_fa=title_fa,
            author=str(book["author"]) if book["author"] else None,
            translator=str(book["translator"]) if book["translator"] else None,
            language=str(book["language"] or "fa"),
            rights_basis=str(book["rights_basis"] or "user_supplied"),
            source_format=str(book["source_format"] or "epub"),
            source_sha256=str(book["source_sha256"]),
            source_path=_resolve_source_path(base, str(book["source_path"])),
            metadata=metadata,
            cover=cover,
            chapters=chapters,
            generated_at=timestamp,
            incomplete=incomplete,
            open_errors=open_errors,
        )
    finally:
        db.close()
