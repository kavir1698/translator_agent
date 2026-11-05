"""SQLite connection, schema bootstrap, migrations and the thin DAL."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import ConfigError, InvalidTransition, RuntimeFailure
from .ids import run_id as new_run_id
from .ids import utc_now_iso
from .models import (
    ChapterStatus,
    PassKind,
    RunStatus,
    SegmentStatus,
    Severity,
)

SCHEMA_VERSION = 1
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")

_SEGMENT_TRANSITIONS: dict[str, set[str]] = {
    SegmentStatus.PENDING: {
        SegmentStatus.DRAFTED,
        SegmentStatus.REVISED,
        SegmentStatus.DONE,
        SegmentStatus.FAILED,
        SegmentStatus.SKIPPED,
    },
    SegmentStatus.DRAFTED: {
        SegmentStatus.REVISED,
        SegmentStatus.DONE,
        SegmentStatus.FAILED,
        SegmentStatus.SKIPPED,
    },
    SegmentStatus.REVISED: {
        SegmentStatus.DONE,
        SegmentStatus.FLAGGED,
        SegmentStatus.FAILED,
        SegmentStatus.SKIPPED,
    },
    SegmentStatus.DONE: {SegmentStatus.FLAGGED, SegmentStatus.REVISED, SegmentStatus.SKIPPED},
    SegmentStatus.FLAGGED: {SegmentStatus.REVISED, SegmentStatus.DONE, SegmentStatus.FAILED},
    SegmentStatus.FAILED: {
        SegmentStatus.PENDING,
        SegmentStatus.DRAFTED,
        SegmentStatus.REVISED,
        SegmentStatus.SKIPPED,
    },
    SegmentStatus.SKIPPED: {SegmentStatus.PENDING},
}

_CHAPTER_TRANSITIONS: dict[str, set[str]] = {
    ChapterStatus.PENDING: {
        ChapterStatus.ANALYZED,
        ChapterStatus.TRANSLATING,
        ChapterStatus.TRANSLATED,
    },
    ChapterStatus.ANALYZED: {ChapterStatus.TRANSLATING, ChapterStatus.TRANSLATED},
    ChapterStatus.TRANSLATING: {ChapterStatus.TRANSLATED},
    ChapterStatus.TRANSLATED: {
        ChapterStatus.AUDITED,
        ChapterStatus.REVIEWED,
        ChapterStatus.FINAL,
    },
    ChapterStatus.AUDITED: {ChapterStatus.REVIEWED, ChapterStatus.FINAL},
    ChapterStatus.REVIEWED: {ChapterStatus.FINAL, ChapterStatus.TRANSLATED},
    ChapterStatus.FINAL: set(),
}

_RUN_TRANSITIONS: dict[str, set[str]] = {
    RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.ABORTED, RunStatus.PAUSED},
    RunStatus.PAUSED: {RunStatus.RUNNING, RunStatus.ABORTED, RunStatus.FAILED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.ABORTED: set(),
}


class Database:
    """One SQLite file per book. Writes are serialized by an RLock."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------- connection
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        return self._conn

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA temp_store=MEMORY")
        self._conn = conn
        return conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> Database:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    # --------------------------------------------------------------- schema
    def initialize(self) -> None:
        with self._lock:
            existing = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if existing is None:
                ddl = _SCHEMA_FILE.read_text(encoding="utf-8")
                script = (
                    "BEGIN IMMEDIATE;\n"
                    f"{ddl}\n"
                    f"INSERT INTO schema_version(version) VALUES ({SCHEMA_VERSION});\n"
                    "COMMIT;"
                )
                try:
                    self.conn.executescript(script)
                except Exception:
                    self.conn.rollback()
                    raise
                return
            version_row = self.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
            version = version_row[0] if version_row else None
            if version is None:
                raise ConfigError(
                    f"{self.path} has an incomplete schema",
                    hint="run `tj init <file> --force` to archive and rebuild the workspace",
                )
            if int(version) > SCHEMA_VERSION:
                raise ConfigError(
                    f"{self.path} was written by a newer Tarjoman schema ({version})",
                    hint="upgrade Tarjoman or use a fresh workspace",
                )
            self._apply_migrations(int(version))

    def schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def _apply_migrations(self, current: int) -> None:
        migrations_dir = self.path.parent / "migrations"
        if not migrations_dir.is_dir():
            return
        pending = sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql"))
        for script in pending:
            version = int(script.name.split("_", 1)[0])
            if version <= current:
                continue
            backup = self.path.with_name(
                f"{self.path.name}.bak-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
            )
            self.conn.execute("PRAGMA wal_checkpoint(FULL)")
            backup_conn = sqlite3.connect(backup)
            try:
                self.conn.backup(backup_conn)
            finally:
                backup_conn.close()
            try:
                sql = script.read_text(encoding="utf-8")
                self.conn.executescript(
                    "BEGIN IMMEDIATE;\n"
                    f"{sql}\n"
                    f"INSERT INTO schema_version(version) VALUES ({version});\n"
                    "COMMIT;"
                )
            except Exception as exc:
                self.conn.rollback()
                raise ConfigError(
                    f"migration {script.name} failed; backup at {backup}",
                    hint="restore the backup or rebuild with `tj init --force`",
                ) from exc

    # ------------------------------------------------------------- raw helpers
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.transaction() as conn:
            cursor = conn.execute(sql, params)
            return int(cursor.lastrowid or 0)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        with self.transaction() as conn:
            conn.executemany(sql, rows)

    def fetchone(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.fetchone(sql, params)
        return row[0] if row else None

    # ----------------------------------------------------------------- books
    def insert_book(
        self,
        *,
        slug: str,
        title_en: str,
        source_path: str,
        source_sha256: str,
        source_format: str = "epub",
        author: str | None = None,
        translator: str | None = None,
        rights_basis: str = "user_supplied",
        import_quality: float | None = None,
        metadata: dict[str, Any] | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        sql = """
            INSERT INTO books (slug, title_en, author, translator, source_path, source_sha256,
                               source_format, import_quality, rights_basis, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        params = (
            slug,
            title_en,
            author,
            translator,
            source_path,
            source_sha256,
            source_format,
            import_quality,
            rights_basis,
            json.dumps(metadata or {}, ensure_ascii=False),
            utc_now_iso(),
        )
        if conn is not None:
            return int(conn.execute(sql, params).lastrowid or 0)
        return self.execute(sql, params)

    def get_book(self, slug: str) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM books WHERE slug = ?", (slug,))

    def get_book_by_id(self, book_id: int) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM books WHERE id = ?", (book_id,))

    def set_book_import_quality(self, book_id: int, quality: float) -> None:
        self.execute("UPDATE books SET import_quality = ? WHERE id = ?", (quality, book_id))

    # -------------------------------------------------------------- chapters
    def insert_chapter(
        self,
        *,
        book_id: int,
        idx: int,
        kind: str,
        title_en: str | None = None,
        source_doc: str | None = None,
        word_count: int = 0,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        sql = """
            INSERT INTO chapters (book_id, idx, kind, title_en, source_doc, word_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """
        params = (book_id, idx, kind, title_en, source_doc, word_count, utc_now_iso())
        if conn is not None:
            return int(conn.execute(sql, params).lastrowid or 0)
        return self.execute(sql, params)

    def get_chapter(self, chapter_id: int) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM chapters WHERE id = ?", (chapter_id,))

    def get_chapter_by_idx(self, book_id: int, idx: int) -> sqlite3.Row | None:
        return self.fetchone(
            "SELECT * FROM chapters WHERE book_id = ? AND idx = ?", (book_id, idx)
        )

    def chapters(self, book_id: int) -> list[sqlite3.Row]:
        return self.fetchall("SELECT * FROM chapters WHERE book_id = ? ORDER BY idx", (book_id,))

    def transition_chapter(self, chapter_id: int, to: str) -> None:
        row = self.get_chapter(chapter_id)
        if row is None:
            raise RuntimeFailure(f"chapter {chapter_id} does not exist")
        current = row["status"]
        if to == current:
            return
        if to not in _CHAPTER_TRANSITIONS.get(current, set()):
            raise InvalidTransition(f"chapter {chapter_id}: {current} -> {to} is not allowed")
        self.execute("UPDATE chapters SET status = ? WHERE id = ?", (to, chapter_id))

    # -------------------------------------------------------------- segments
    def insert_segment(
        self,
        *,
        segment_id: str,
        chapter_id: int,
        idx: int,
        kind: str,
        source_text: str,
        source_html: str,
        nodes_json: str = "[]",
        content_hash: str = "",
        tokens_est: int = 0,
        sentence_count: int = 0,
        dialogue_ratio: float = 0.0,
        lang_mix: float = 0.0,
        split_of: str | None = None,
        status: str = SegmentStatus.PENDING,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        sql = """
            INSERT INTO segments (id, chapter_id, idx, kind, source_text, source_html, nodes_json,
                                  content_hash, status, tokens_est, sentence_count, dialogue_ratio,
                                  lang_mix, split_of, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
        params = (
            segment_id,
            chapter_id,
            idx,
            kind,
            source_text,
            source_html,
            nodes_json,
            content_hash,
            status,
            tokens_est,
            sentence_count,
            dialogue_ratio,
            lang_mix,
            split_of,
            utc_now_iso(),
        )
        if conn is not None:
            conn.execute(sql, params)
            return
        self.execute(sql, params)

    def get_segment(self, segment_id: str) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM segments WHERE id = ?", (segment_id,))

    def segments(self, chapter_id: int | None = None, book_id: int | None = None) -> list[sqlite3.Row]:
        if chapter_id is not None:
            return self.fetchall(
                "SELECT * FROM segments WHERE chapter_id = ? ORDER BY idx", (chapter_id,)
            )
        if book_id is not None:
            return self.fetchall(
                """
                SELECT s.* FROM segments s JOIN chapters c ON c.id = s.chapter_id
                WHERE c.book_id = ? ORDER BY c.idx, s.idx
                """,
                (book_id,),
            )
        return self.fetchall("SELECT * FROM segments ORDER BY chapter_id, idx")

    def segments_with_status(self, statuses: Sequence[str], book_id: int) -> list[sqlite3.Row]:
        marks = ",".join("?" for _ in statuses)
        return self.fetchall(
            f"""
            SELECT s.* FROM segments s JOIN chapters c ON c.id = s.chapter_id
            WHERE c.book_id = ? AND s.status IN ({marks})
            ORDER BY c.idx, s.idx
            """,
            (book_id, *statuses),
        )

    def set_segment_risk(self, segment_id: str, risk_score: int) -> None:
        self.execute(
            "UPDATE segments SET risk_score = ? WHERE id = ?", (max(0, min(100, risk_score)), segment_id)
        )

    def transition_segment(self, segment_id: str, to: str) -> None:
        row = self.get_segment(segment_id)
        if row is None:
            raise RuntimeFailure(f"segment {segment_id} does not exist")
        current = row["status"]
        if to == current:
            return
        if to not in _SEGMENT_TRANSITIONS.get(current, set()):
            raise InvalidTransition(f"segment {segment_id}: {current} -> {to} is not allowed")
        self.execute("UPDATE segments SET status = ? WHERE id = ?", (to, segment_id))

    # ---------------------------------------------------------- translations
    def add_translation(
        self,
        *,
        segment_id: str,
        pass_kind: str,
        fa_text: str,
        fa_markup: str,
        model: str,
        notes: dict[str, Any] | None = None,
        glossary_hash: str | None = None,
        glossary_terms: Sequence[str] = (),
        prompt_hash: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
        version: int | None = None,
        make_current: bool = True,
    ) -> int:
        if pass_kind not in {item.value for item in PassKind}:
            raise RuntimeFailure(f"unknown translation pass: {pass_kind}")
        with self.transaction() as conn:
            if version is None:
                row = conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM translations WHERE segment_id = ?",
                    (segment_id,),
                ).fetchone()
                version = int(row[0]) + 1
            if make_current:
                conn.execute(
                    "UPDATE translations SET is_current = 0 WHERE segment_id = ? AND is_current = 1",
                    (segment_id,),
                )
            conn.execute(
                """
                INSERT INTO translations (segment_id, version, is_current, pass, fa_text, fa_markup,
                                          notes_json, glossary_hash, glossary_terms_json, model,
                                          prompt_hash, tokens_in, tokens_out, cost_usd, latency_ms,
                                          created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    version,
                    1 if make_current else 0,
                    pass_kind,
                    fa_text,
                    fa_markup,
                    json.dumps(notes or {}, ensure_ascii=False),
                    glossary_hash,
                    json.dumps(list(glossary_terms), ensure_ascii=False),
                    model,
                    prompt_hash,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                    latency_ms,
                    utc_now_iso(),
                ),
            )
            return int(version)

    def current_translation(self, segment_id: str) -> sqlite3.Row | None:
        return self.fetchone(
            "SELECT * FROM translations WHERE segment_id = ? AND is_current = 1", (segment_id,)
        )

    def translation_versions(self, segment_id: str) -> list[sqlite3.Row]:
        return self.fetchall(
            "SELECT * FROM translations WHERE segment_id = ? ORDER BY version", (segment_id,)
        )

    # ----------------------------------------------------------------- issues
    def upsert_issue(
        self,
        *,
        stage: str,
        code: str,
        dedupe_key: str,
        severity: str,
        message: str,
        segment_id: str | None = None,
        chapter_id: int | None = None,
        quote_source: str = "",
        quote_target: str = "",
        context: dict[str, Any] | None = None,
    ) -> int:
        if severity not in {item.value for item in Severity}:
            raise RuntimeFailure(f"unknown severity: {severity}")
        if not dedupe_key.startswith(("seg:", "ch:")):
            raise RuntimeFailure(f"dedupe_key must be namespaced with seg:/ch:: {dedupe_key!r}")
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO issues (segment_id, chapter_id, stage, code, severity, message,
                                    quote_source, quote_target, context_json, dedupe_key, status,
                                    created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                ON CONFLICT(stage, code, dedupe_key) DO UPDATE SET
                    severity = excluded.severity,
                    message = excluded.message,
                    quote_source = excluded.quote_source,
                    quote_target = excluded.quote_target,
                    context_json = excluded.context_json
                """,
                (
                    segment_id,
                    chapter_id,
                    stage,
                    code,
                    severity,
                    message,
                    quote_source,
                    quote_target,
                    json.dumps(context or {}, ensure_ascii=False),
                    dedupe_key,
                    utc_now_iso(),
                ),
            )
            row = conn.execute(
                "SELECT id FROM issues WHERE stage = ? AND code = ? AND dedupe_key = ?",
                (stage, code, dedupe_key),
            ).fetchone()
            return int(row[0])

    def set_issue_status(
        self, issue_id: int, status: str, *, resolved_by: str | None = None, resolution: str | None = None
    ) -> None:
        if status not in ("open", "resolved", "waived"):
            raise RuntimeFailure(f"unknown issue status: {status}")
        if status == "waived" and (not resolved_by or not resolution):
            raise RuntimeFailure("waiving an issue requires a reviewer and a reason")
        self.execute(
            """
            UPDATE issues SET status = ?, resolved_by = ?, resolution = ?, resolved_at = ?
            WHERE id = ?
            """,
            (status, resolved_by, resolution, utc_now_iso() if status != "open" else None, issue_id),
        )

    def issues_for(
        self, *, segment_id: str | None = None, status: str | None = None
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM issues WHERE 1=1"
        params: list[Any] = []
        if segment_id is not None:
            sql += " AND segment_id = ?"
            params.append(segment_id)
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        return self.fetchall(sql + " ORDER BY id", params)

    # ------------------------------------------------------------------- runs
    def start_run(
        self,
        *,
        book_id: int,
        stage: str,
        config_json: str,
        window_plan_hash: str | None = None,
        artifacts_hash: dict[str, str] | None = None,
        run: str | None = None,
    ) -> str:
        identifier = run or new_run_id()
        self.execute(
            """
            INSERT INTO runs (id, book_id, stage, status, config_json, window_plan_hash,
                              artifacts_hash_json, stats_json, started_at)
            VALUES (?, ?, ?, 'running', ?, ?, ?, '{}', ?)
            """,
            (
                identifier,
                book_id,
                stage,
                config_json,
                window_plan_hash,
                json.dumps(artifacts_hash or {}, ensure_ascii=False),
                utc_now_iso(),
            ),
        )
        return identifier

    def get_run(self, identifier: str) -> sqlite3.Row | None:
        return self.fetchone("SELECT * FROM runs WHERE id = ?", (identifier,))

    def update_run(
        self,
        identifier: str,
        *,
        stats: dict[str, Any] | None = None,
        cost_usd: float | None = None,
    ) -> None:
        row = self.get_run(identifier)
        if row is None:
            raise RuntimeFailure(f"run {identifier} does not exist")
        if stats is not None:
            try:
                merged = json.loads(row["stats_json"] or "{}")
            except json.JSONDecodeError:
                merged = {}
            if not isinstance(merged, dict):
                merged = {}
            merged.update(stats)
            self.execute(
                "UPDATE runs SET stats_json = ? WHERE id = ?",
                (json.dumps(merged, ensure_ascii=False), identifier),
            )
        if cost_usd is not None:
            self.execute("UPDATE runs SET cost_usd = ? WHERE id = ?", (cost_usd, identifier))

    def transition_run(self, identifier: str, to: str) -> None:
        row = self.get_run(identifier)
        if row is None:
            raise RuntimeFailure(f"run {identifier} does not exist")
        current = row["status"]
        if to == current:
            return
        if to not in _RUN_TRANSITIONS.get(current, set()):
            raise InvalidTransition(f"run {identifier}: {current} -> {to} is not allowed")
        self.execute(
            "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
            (to, utc_now_iso() if to != RunStatus.RUNNING else None, identifier),
        )

    def abort_orphan_runs(self, book_id: int, timeout_s: int = 180) -> int:
        """Runs left 'running' with a stale (or missing) heartbeat are aborted."""
        cutoff = datetime.now(UTC).timestamp() - (2 * timeout_s)
        count = 0
        for row in self.fetchall(
            "SELECT * FROM runs WHERE book_id = ? AND status = 'running'", (book_id,)
        ):
            try:
                stats = json.loads(row["stats_json"])
            except json.JSONDecodeError:
                stats = {}
            heartbeat = stats.get("heartbeat_at") if isinstance(stats, dict) else None
            reference = heartbeat or row["started_at"]
            try:
                stamp = datetime.fromisoformat(str(reference).replace("Z", "+00:00")).timestamp()
            except ValueError:
                stamp = 0.0
            if stamp > cutoff:
                continue
            self.execute(
                "UPDATE runs SET status = 'aborted', finished_at = ? WHERE id = ?",
                (utc_now_iso(), row["id"]),
            )
            count += 1
        return count

    # -------------------------------------------------------------- llm_calls
    def record_llm_call(
        self,
        *,
        run_id_value: str | None,
        agent: str,
        model: str,
        prompt_hash: str,
        input_hash: str,
        cached: bool = False,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        latency_ms: int = 0,
        retries: int = 0,
        finish_reason: str | None = None,
        error: str | None = None,
    ) -> int:
        return self.execute(
            """
            INSERT INTO llm_calls (run_id, agent, model, prompt_hash, input_hash, cached, tokens_in,
                                   tokens_out, cost_usd, latency_ms, retries, finish_reason, error,
                                   created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id_value,
                agent,
                model,
                prompt_hash,
                input_hash,
                1 if cached else 0,
                tokens_in,
                tokens_out,
                cost_usd,
                latency_ms,
                retries,
                finish_reason,
                error,
                utc_now_iso(),
            ),
        )

    # ------------------------------------------------------------------ cache
    def cache_get(self, key: str) -> str | None:
        row = self.fetchone("SELECT value_json FROM cache WHERE key = ?", (key,))
        return str(row["value_json"]) if row is not None else None

    def cache_put(self, key: str, value_json: str) -> None:
        self.execute(
            """
            INSERT INTO cache (key, value_json, created_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
            """,
            (key, value_json, utc_now_iso()),
        )

    # ------------------------------------------------------------------ stats
    def counts(self, book_id: int) -> dict[str, int]:
        return {
            "chapters": int(
                self.scalar("SELECT COUNT(*) FROM chapters WHERE book_id = ?", (book_id,)) or 0
            ),
            "segments": int(
                self.scalar(
                    "SELECT COUNT(*) FROM segments s JOIN chapters c ON c.id = s.chapter_id "
                    "WHERE c.book_id = ?",
                    (book_id,),
                )
                or 0
            ),
            "translations": int(
                self.scalar(
                    "SELECT COUNT(*) FROM translations t JOIN segments s ON s.id = t.segment_id "
                    "JOIN chapters c ON c.id = s.chapter_id WHERE c.book_id = ?",
                    (book_id,),
                )
                or 0
            ),
        }
