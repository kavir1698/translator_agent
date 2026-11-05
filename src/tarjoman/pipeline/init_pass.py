"""`tj init`: extract a source file into a book workspace and segment it."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from tarjoman.core.config import Settings
from tarjoman.core.db import Database
from tarjoman.core.errors import ConfigError
from tarjoman.core.ids import segment_id, sha256_file, sha256_text, slugify
from tarjoman.core.models import SegmentStatus
from tarjoman.core.paths import book_dir
from tarjoman.core.tokens import (
    dialogue_ratio,
    estimate_tokens_en,
    lang_mix,
    sentence_count,
)
from tarjoman.ingest.blocks import TRANSLATABLE, Block
from tarjoman.ingest.chapterize import ChapterPlan, chapterize, load_structure
from tarjoman.ingest.normalize_source import normalize_source
from tarjoman.ingest.reader import read_source
from tarjoman.persian.lint import protected_latin

_SKIP_TEXT = re.compile(r"^[\d\s.,،؛:()\[\]\-–—_/]+$")


@dataclass
class InitResult:
    slug: str
    book_id: int
    chapters: int
    segments: int
    words: int
    import_quality: float | None
    created: bool
    db_path: Path
    source_format: str


def _workspace_dirs(base: Path) -> None:
    for name in ("source", "artifacts", "runs", "review", "out"):
        (base / name).mkdir(parents=True, exist_ok=True)


def _archive_workspace(base: Path, root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    backups = root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"{base.name}.bak-{stamp}"
    shutil.copytree(base, backup)
    return backup


def _source_sidecar_path(base: Path) -> Path:
    return base / "source" / "blocks.json"


def _marker_path(base: Path) -> Path:
    return base / "source" / "init.json"


def _serialize_chapters(chapters: list[ChapterPlan]) -> dict[str, Any]:
    return {
        "version": 1,
        "chapters": [
            {
                "idx": index,
                "kind": chapter.kind.value,
                "title_en": chapter.title_en,
                "source_doc": chapter.source_doc,
                "skip_translation": chapter.skip_translation,
                "word_count": chapter.word_count,
                "blocks": [block.to_json() for block in chapter.blocks],
            }
            for index, chapter in enumerate(chapters, start=1)
        ],
    }


def _segment_status(block: Block, chapter: ChapterPlan) -> str:
    text = block.text.strip()
    if chapter.skip_translation:
        return SegmentStatus.SKIPPED.value
    if len(text) < 2 or _SKIP_TEXT.match(text):
        return SegmentStatus.SKIPPED.value
    if text.casefold() in {word.casefold() for word in protected_latin()}:
        return SegmentStatus.SKIPPED.value
    return SegmentStatus.PENDING.value


def _write_book_config(base: Path, glossary_path: str | None) -> None:
    if not glossary_path:
        return
    path = base / "config.yaml"
    data: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    data.setdefault("glossary", {})
    data["glossary"]["mode"] = "provided"
    data["glossary"]["path"] = str(Path(glossary_path).resolve())
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _marker_matches(base: Path, source_hash: str, counts: dict[str, Any]) -> bool:
    path = _marker_path(base)
    if not path.is_file():
        return False
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return (
        marker.get("source_sha256") == source_hash
        and marker.get("chapters") == counts["chapters"]
        and marker.get("segments") == counts["segments"]
    )


def _write_marker(base: Path, source_hash: str, counts: dict[str, Any]) -> None:
    _marker_path(base).write_text(
        json.dumps({"source_sha256": source_hash, **counts}, indent=2), encoding="utf-8"
    )


def init_book(
    *,
    root: Path,
    source_path: str | Path,
    settings: Settings,
    slug: str | None = None,
    force: bool = False,
    glossary_path: str | None = None,
) -> InitResult:
    source = Path(source_path)
    if not source.is_file():
        raise ConfigError(f"source file not found: {source}")
    if glossary_path and not Path(glossary_path).is_file():
        raise ConfigError(f"glossary file not found: {glossary_path}")

    source_hash = sha256_file(source)
    result = read_source(source)
    if not any(block.text.strip() for block in result.all_blocks()):
        raise ConfigError(
            f"{source} contains no translatable text",
            hint="check the file encoding or supply a text-layer PDF/EPUB",
        )
    chosen_slug = slug or settings.book.slug or slugify(result.title)
    base = book_dir(chosen_slug, root, settings.project.books_dir)
    db_path = base / "book.db"

    structure = None
    existing_structure = base / "artifacts" / "structure.yaml"
    if existing_structure.is_file():
        structure = load_structure(existing_structure)

    if base.exists() and force:
        _archive_workspace(base, root)
        shutil.rmtree(base)
    elif db_path.exists():
        with Database(db_path) as database:
            book = database.get_book(chosen_slug)
        if book is not None and book["source_sha256"] == source_hash:
            counts = _counts(db_path)
            if _marker_matches(base, source_hash, counts):
                return InitResult(
                    slug=chosen_slug,
                    book_id=int(book["id"]),
                    chapters=counts["chapters"],
                    segments=counts["segments"],
                    words=counts["words"],
                    import_quality=book["import_quality"],
                    created=False,
                    db_path=db_path,
                    source_format=result.source_format,
                )
            raise ConfigError(
                f"{chosen_slug} has an incomplete ingest",
                hint="re-run with --force to archive and rebuild the workspace",
            )
        if book is not None:
            raise ConfigError(
                f"{chosen_slug} is already initialized from a different file",
                hint="re-run with --force to archive the old workspace and rebuild it",
            )
        raise ConfigError(
            f"{chosen_slug} has a database but no book row",
            hint="re-run with --force to archive and rebuild the workspace",
        )

    _workspace_dirs(base)
    if structure is not None:
        (base / "artifacts" / "structure.yaml").write_text(
            yaml.safe_dump(structure, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
    chapters = chapterize(result, structure=structure)

    suffix = Path(source.name).suffix or f".{result.source_format}"
    original = base / "source" / f"original{suffix}"
    shutil.copy2(source, original)
    (base / "source" / "metadata.json").write_text(
        json.dumps(
            {
                "title": result.title,
                "author": result.author,
                "language": result.language,
                "publisher": result.publisher,
                "rights": result.rights,
                "identifier": result.identifier,
                "cover": result.cover,
                "source_format": result.source_format,
                "source_sha256": source_hash,
                "import_quality": result.import_quality,
                "metadata": result.metadata,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _source_sidecar_path(base).write_text(
        json.dumps(_serialize_chapters(chapters), ensure_ascii=False), encoding="utf-8"
    )
    _write_book_config(base, glossary_path)

    database = Database(db_path)
    database.initialize()
    try:
        with database.transaction() as conn:
            book_id = database.insert_book(
                slug=chosen_slug,
                title_en=result.title,
                author=result.author,
                translator=settings.book.translator,
                source_path=original.relative_to(base).as_posix(),
                source_sha256=source_hash,
                source_format=result.source_format,
                rights_basis=settings.book.rights_basis,
                import_quality=result.import_quality,
                metadata={
                    "title": result.title,
                    "language": result.language,
                    "ingest_complete": True,
                },
                conn=conn,
            )
            total_segments = 0
            total_words = 0
            for chapter_index, chapter in enumerate(chapters, start=1):
                chapter_id = database.insert_chapter(
                    book_id=book_id,
                    idx=chapter_index,
                    kind=chapter.kind.value,
                    title_en=chapter.title_en,
                    source_doc=chapter.source_doc,
                    word_count=chapter.word_count,
                    conn=conn,
                )
                total_words += chapter.word_count
                segment_index = 0
                for block in chapter.blocks:
                    if block.kind not in TRANSLATABLE or not block.text.strip():
                        continue
                    segment_index += 1
                    text = normalize_source(block.text)
                    database.insert_segment(
                        segment_id=segment_id(chapter_index, segment_index),
                        chapter_id=chapter_id,
                        idx=segment_index,
                        kind=block.kind.value,
                        source_text=text,
                        source_html=block.html,
                        nodes_json=json.dumps(block.nodes, ensure_ascii=False),
                        content_hash=sha256_text(text),
                        tokens_est=estimate_tokens_en(text),
                        sentence_count=sentence_count(text),
                        dialogue_ratio=dialogue_ratio(text),
                        lang_mix=lang_mix(text),
                        status=_segment_status(block, chapter),
                        conn=conn,
                    )
                    total_segments += 1
    finally:
        database.close()

    counts = {"chapters": len(chapters), "segments": total_segments, "words": total_words}
    _write_marker(base, source_hash, counts)
    return InitResult(
        slug=chosen_slug,
        book_id=book_id,
        chapters=len(chapters),
        segments=total_segments,
        words=total_words,
        import_quality=result.import_quality,
        created=True,
        db_path=db_path,
        source_format=result.source_format,
    )


def _counts(db_path: Path) -> dict[str, Any]:
    with Database(db_path) as database:
        book = database.fetchone("SELECT id FROM books LIMIT 1")
        if book is None:
            return {"chapters": 0, "segments": 0, "words": 0}
        chapters = int(
            database.scalar(
                "SELECT COUNT(*) FROM chapters WHERE book_id = ?", (book["id"],)
            )
            or 0
        )
        segments = int(
            database.scalar(
                "SELECT COUNT(*) FROM segments s JOIN chapters c ON c.id = s.chapter_id "
                "WHERE c.book_id = ?",
                (book["id"],),
            )
            or 0
        )
        words = int(
            database.scalar(
                "SELECT COALESCE(SUM(word_count), 0) FROM chapters WHERE book_id = ?", (book["id"],)
            )
            or 0
        )
    return {"chapters": chapters, "segments": segments, "words": words}
