"""`shz inspect` — chapter/segment/token/markup statistics before spending."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from shahrzad.core.config import load_settings
from shahrzad.core.db import Database
from shahrzad.core.errors import RuntimeFailure
from shahrzad.core.ids import sha256_bytes
from shahrzad.core.paths import find_book_dir, project_root

from .ui import console, handle_errors


@handle_errors
def inspect(
    ctx: typer.Context,
    json_out: bool = typer.Option(False, "--json", help="Machine-readable output."),
    diff: bool = typer.Option(False, "--diff", help="Compare stored source hashes with the file."),
) -> None:
    """Report chapters, segments, tokens, markup nodes and import quality."""
    root = project_root()
    slug = ctx.obj.get("book") if ctx.obj else None
    books_name = load_settings(root=root).project.books_dir
    base = find_book_dir(root, slug, books_name)
    db_path = base / "book.db"
    with Database(db_path) as db:
        book = db.fetchone("SELECT * FROM books LIMIT 1")
        if book is None:
            console.print("[red]the workspace has no book row[/red]")
            raise typer.Exit(1)
        book_id = int(book["id"])
        chapters = db.fetchall(
            "SELECT * FROM chapters WHERE book_id = ? ORDER BY idx", (book_id,)
        )
        segments = db.fetchall(
            """
            SELECT s.* FROM segments s JOIN chapters c ON c.id = s.chapter_id
            WHERE c.book_id = ? ORDER BY c.idx, s.idx
            """,
            (book_id,),
        )

    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    tokens = 0
    markup_nodes = 0
    for segment in segments:
        by_status[segment["status"]] = by_status.get(segment["status"], 0) + 1
        by_kind[segment["kind"]] = by_kind.get(segment["kind"], 0) + 1
        tokens += int(segment["tokens_est"])
        try:
            nodes = json.loads(segment["nodes_json"])
        except json.JSONDecodeError:
            nodes = []
        markup_nodes += sum(1 for node in nodes if node.get("path") or node.get("kind") == "atomic")

    chapters_by_status: dict[str, int] = {}
    words = 0
    chapter_rows = []
    for chapter in chapters:
        chapters_by_status[chapter["status"]] = chapters_by_status.get(chapter["status"], 0) + 1
        words += int(chapter["word_count"])
        chapter_rows.append(
            {
                "idx": chapter["idx"],
                "title": chapter["title_en"] or "",
                "kind": chapter["kind"],
                "status": chapter["status"],
                "words": int(chapter["word_count"]),
                "segments": sum(1 for segment in segments if segment["chapter_id"] == chapter["id"]),
            }
        )

    payload = {
        "slug": book["slug"],
        "title": book["title_en"],
        "author": book["author"],
        "source_format": book["source_format"],
        "import_quality": book["import_quality"],
        "rights_basis": book["rights_basis"],
        "words": words,
        "chapters": len(chapters),
        "segments": len(segments),
        "tokens_est": tokens,
        "markup_nodes": markup_nodes,
        "segments_by_status": by_status,
        "segments_by_kind": by_kind,
        "chapters_by_status": chapters_by_status,
        "chapter_rows": chapter_rows,
    }

    if diff:
        payload["diff"] = _diff(base, book, segments)

    if json_out:
        typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        return

    quality = (
        f" · import quality [bold]{book['import_quality']:.2f}[/bold]"
        if book["import_quality"] is not None
        else ""
    )
    console.print(
        f"[bold]{book['title_en']}[/bold] — {book['slug']} ({book['source_format']}){quality}"
    )
    console.print(
        f"{words:,} words · {len(chapters)} chapters · {len(segments)} segments · "
        f"~{tokens:,} source tokens · {markup_nodes} inline nodes"
    )
    console.print(
        "segments: "
        + " · ".join(f"{status}={count}" for status, count in sorted(by_status.items()))
    )

    table = Table(title="Chapters", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("title", overflow="fold")
    table.add_column("kind")
    table.add_column("status")
    table.add_column("words", justify="right")
    table.add_column("segments", justify="right")
    for row in chapter_rows:
        table.add_row(
            str(row["idx"]),
            row["title"],
            row["kind"],
            row["status"],
            f"{row['words']:,}",
            str(row["segments"]),
        )
    console.print(table)

    if diff:
        info = payload["diff"]
        if info.get("source_missing"):
            console.print(f"diff: stored source is missing ({info['stored_path']})")
        else:
            console.print(
                f"diff: stored source hash "
                f"{'matches' if info['source_unchanged'] else 'DIFFERS from'} "
                f"the extracted file ({info['stored_sha256'][:12]}…)"
            )


def _diff(base: Path, book: Any, segments: list[Any]) -> dict[str, object]:
    stored = str(book["source_sha256"])
    relative = str(book["source_path"])
    original = (base / relative).resolve()
    if not original.is_relative_to(base.resolve()):
        raise RuntimeFailure(f"stored source path escapes the workspace: {relative}")
    if not original.is_file():
        return {
            "stored_sha256": stored,
            "stored_path": relative,
            "source_missing": True,
            "source_unchanged": False,
            "segments": len(segments),
        }
    changed = sha256_bytes(original.read_bytes()) != stored
    return {
        "stored_sha256": stored,
        "stored_path": relative,
        "source_missing": False,
        "source_unchanged": not changed,
        "segments": len(segments),
    }
