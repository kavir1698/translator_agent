"""`shz analyze`: deterministic sample, brief, summaries and optional glossary (plan 6.1)."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from shahrzad.agents.analyst import (
    BriefAnalyst,
    brief_hash,
    freeze_brief,
    load_approved_brief,
    load_brief_draft,
    validate_brief,
    write_brief_draft,
)
from shahrzad.agents.entity_extractor import EntityExtractor
from shahrzad.agents.glossary_proposer import GlossaryProposer
from shahrzad.agents.summarizer import ChapterSummarizer
from shahrzad.core.config import Settings, redacted_config
from shahrzad.core.cost import ModelProfile
from shahrzad.core.db import Database
from shahrzad.core.errors import ConfigError
from shahrzad.core.glossary import normalize_term_en
from shahrzad.core.ids import sha256_file, sha256_text, utc_now_iso
from shahrzad.core.models import ChapterStatus, Entity
from shahrzad.core.paths import find_book_dir
from shahrzad.core.text import compute_style_metrics
from shahrzad.llm.cache import ResponseCache
from shahrzad.llm.promptlib import load as load_prompt
from shahrzad.llm.provider import Provider

from .glossary_ops import (
    import_provided_glossary,
    write_glossary_artifact,
)

PROMPTS = ("p01_brief", "p02_summarizer", "p03_entities", "p04_glossary")


@dataclass(frozen=True)
class SamplePart:
    reason: str
    segment_id: str
    chapter_idx: int
    tokens: int
    text: str


@dataclass(frozen=True)
class Sample:
    parts: list[SamplePart]
    text: str
    tokens: int
    manifest: dict[str, Any]


@dataclass
class AnalyzeResult:
    run_id: str | None = None
    brief_path: Path | None = None
    draft_path: Path | None = None
    frozen: bool = False
    paused_for_brief: bool = False
    summaries: int = 0
    entities: int = 0
    proposals: int = 0
    glossary_entries: int = 0
    proper_nouns: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    cached_calls: int = 0
    mode: str = "off"


def _per_part_limit(
    selected: dict[str, SamplePart],
    segments: Sequence[Any],
    *,
    chapter_idx: int | None,
    chapters: Sequence[Any] | None = None,
    reason: str,
    budget: int,
    used: int,
    limit: int | None,
) -> tuple[int, int]:
    """Add unselected segments up to a per-part token limit; return (used, added)."""
    part_tokens = 0
    for segment in segments:
        segment_id = str(segment["id"])
        if segment_id in selected:
            continue
        tokens = max(1, int(segment["tokens_est"]))
        if used + tokens > budget:
            break
        if limit is not None and part_tokens + tokens > limit:
            break
        resolved_chapter = (
            chapter_idx
            if chapter_idx is not None
            else _chapter_idx(chapters or [], int(segment["chapter_id"]))
        )
        selected[segment_id] = SamplePart(
            reason=reason,
            segment_id=segment_id,
            chapter_idx=resolved_chapter,
            tokens=tokens,
            text=str(segment["source_text"]),
        )
        used += tokens
        part_tokens += tokens
    return used, part_tokens


def build_sample(db: Database, book_id: int, *, budget: int) -> Sample:
    """Deterministic sample builder from plan 6.1.1, bounded by ``budget`` tokens."""
    chapters = db.chapters(book_id)
    segments_by_chapter: dict[int, list[Any]] = {
        int(chapter["id"]): db.segments(chapter_id=int(chapter["id"])) for chapter in chapters
    }
    all_segments: list[Any] = [
        segment
        for chapter in chapters
        for segment in segments_by_chapter[int(chapter["id"])]
        if str(segment["source_text"]).strip()
    ]
    if not all_segments:
        return Sample(parts=[], text="", tokens=0, manifest={"tokens": 0, "parts": []})

    body = [chapter for chapter in chapters if chapter["kind"] == "body"] or chapters
    selected: dict[str, SamplePart] = {}
    used = 0

    opening_segments = [
        segment
        for chapter in body
        for segment in segments_by_chapter[int(chapter["id"])]
        if str(segment["source_text"]).strip()
    ]
    used, _ = _per_part_limit(
        selected,
        opening_segments,
        chapter_idx=None,
        chapters=chapters,
        reason="opening",
        budget=budget,
        used=used,
        limit=4000,
    )

    first = body[0]
    first_segments = segments_by_chapter[int(first["id"])]
    first_tokens = sum(max(1, int(segment["tokens_est"])) for segment in first_segments)
    first_limit = None if first_tokens < 6000 else 4000
    used, _ = _per_part_limit(
        selected,
        first_segments,
        chapter_idx=int(first["idx"]),
        reason="first_chapter",
        budget=budget,
        used=used,
        limit=first_limit,
    )

    remaining = [chapter for chapter in body if int(chapter["id"]) != int(first["id"])]
    if remaining:
        count = min(8, len(remaining))
        if count == 1:
            picks = [0]
        else:
            picks = sorted(
                {
                    round(index * (len(remaining) - 1) / (count - 1))
                    for index in range(count)
                }
            )
        for pick in picks:
            chapter = remaining[pick]
            segments = segments_by_chapter[int(chapter["id"])]
            used, _ = _per_part_limit(
                selected,
                segments,
                chapter_idx=int(chapter["idx"]),
                reason="chapter_sample_start",
                budget=budget,
                used=used,
                limit=500,
            )
            total_tokens = sum(max(1, int(segment["tokens_est"])) for segment in segments)
            cursor = 0
            midpoint = total_tokens / 2
            for index, segment in enumerate(segments):
                cursor += max(1, int(segment["tokens_est"]))
                if cursor >= midpoint:
                    used, _ = _per_part_limit(
                        selected,
                        segments[index:],
                        chapter_idx=int(chapter["idx"]),
                        reason="chapter_sample_middle",
                        budget=budget,
                        used=used,
                        limit=300,
                    )
                    break

    def add_dense(reverse: bool) -> None:
        nonlocal used
        ordered = sorted(
            all_segments,
            key=lambda segment: (
                -float(segment["dialogue_ratio"])
                if reverse
                else float(segment["dialogue_ratio"]),
                str(segment["id"]),
            ),
        )
        added = 0
        local_seen: Counter[int] = Counter()
        for segment in ordered:
            if added >= 12:
                break
            chapter_idx = _chapter_idx(chapters, int(segment["chapter_id"]))
            if local_seen[chapter_idx] >= 2:
                continue
            if str(segment["id"]) in selected:
                continue
            tokens = max(1, int(segment["tokens_est"]))
            if used + tokens > budget:
                continue
            selected[str(segment["id"])] = SamplePart(
                reason="dialogue_dense" if reverse else "narration_dense",
                segment_id=str(segment["id"]),
                chapter_idx=chapter_idx,
                tokens=tokens,
                text=str(segment["source_text"]),
            )
            used += tokens
            added += 1
            local_seen[chapter_idx] += 1

    add_dense(True)
    add_dense(False)

    verse_segments = [
        segment for segment in all_segments if str(segment["kind"]) in {"verse", "quote"}
    ][:3]
    for segment in verse_segments:
        if str(segment["id"]) in selected:
            continue
        tokens = max(1, int(segment["tokens_est"]))
        if used + tokens > budget:
            break
        selected[str(segment["id"])] = SamplePart(
            reason="verse_or_quote",
            segment_id=str(segment["id"]),
            chapter_idx=_chapter_idx(chapters, int(segment["chapter_id"])),
            tokens=tokens,
            text=str(segment["source_text"]),
        )
        used += tokens

    for chapter in chapters:
        if chapter["kind"] != "front":
            continue
        used, _ = _per_part_limit(
            selected,
            segments_by_chapter[int(chapter["id"])],
            chapter_idx=int(chapter["idx"]),
            reason="front_matter",
            budget=budget,
            used=used,
            limit=None,
        )

    parts = sorted(selected.values(), key=lambda part: (part.chapter_idx, part.segment_id))
    text = "\n\n".join(
        f"### {part.segment_id} · chapter {part.chapter_idx}\n{part.text}" for part in parts
    )
    manifest = {
        "token_budget": budget,
        "tokens": used,
        "text_sha256": sha256_text(text),
        "parts": [
            {
                "segment_id": part.segment_id,
                "chapter": part.chapter_idx,
                "tokens": part.tokens,
                "reason": part.reason,
            }
            for part in parts
        ],
    }
    return Sample(parts=parts, text=text, tokens=used, manifest=manifest)


def _chapter_idx(chapters: Sequence[Any], chapter_id: int) -> int:
    for chapter in chapters:
        if int(chapter["id"]) == chapter_id:
            return int(chapter["idx"])
    return 0


def _persist_glossary_mode(base: Path, mode: str) -> None:
    path = base / "config.yaml"
    data: dict[str, Any] = {}
    if path.is_file():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    data.setdefault("glossary", {})
    data["glossary"]["mode"] = mode
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _artifact_hash(path: Path) -> str:
    return f"sha256:{sha256_file(path)}"


def _artifact_hashes(base: Path) -> dict[str, str]:
    names = ("brief", "glossary", "proper_nouns", "chapter_summaries")
    result: dict[str, str] = {}
    for name in names:
        path = base / "artifacts" / f"{name}.yaml"
        if name == "chapter_summaries":
            path = base / "artifacts" / "chapter_summaries.jsonl"
        if path.is_file():
            result[name] = _artifact_hash(path)
    return result


def _write_calls(base: Path, run_id: str, db: Database) -> None:
    path = base / "runs" / run_id / "calls.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = db.calls_for_run(run_id)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def _candidate_yaml(
    base: Path,
    *,
    book_slug: str,
    entities: Sequence[Entity],
    proposals: Sequence[Any],
    rows: Sequence[Any],
) -> Path:
    by_key = {normalize_term_en(str(row["term_en"])): row for row in rows}
    proposal_map = {
        normalize_term_en(str(proposal.en)): proposal for proposal in proposals
    }
    candidates = []
    for entity in entities:
        kind = entity.kind.value if hasattr(entity.kind, "value") else str(entity.kind)
        key = normalize_term_en(entity.en)
        proposal = proposal_map.get(key)
        row = by_key.get(key)
        candidates.append(
            {
                "en": entity.en,
                "kind": kind,
                "count": entity.count,
                "surface_forms": list(entity.surface_forms),
                "proposed_fa": proposal.fa if proposal is not None else None,
                "alternatives": list(proposal.alternatives) if proposal is not None else [],
                "confidence": float(proposal.confidence) if proposal is not None else 0.0,
                "rationale": proposal.rationale if proposal is not None else "",
                "status": str(row["status"]) if row is not None else "proposed",
            }
        )
    payload = {
        "version": 1,
        "generated_at": utc_now_iso(),
        "book_slug": book_slug,
        "candidates": candidates,
    }
    path = base / "artifacts" / "glossary.candidates.yaml"
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return path


def _write_summaries(base: Path, db: Database, book_id: int) -> Path:
    rows = db.chapter_summaries(book_id)
    path = base / "artifacts" / "chapter_summaries.jsonl"
    lines = []
    for row in rows:
        lines.append(
            json.dumps(
                {
                    "chapter_id": int(row["chapter_id"]),
                    "idx": int(row["idx"]),
                    "summary_en": str(row["summary_en"]),
                    "continuity": json.loads(row["continuity_json"] or "{}"),
                    "tone_notes": str(row["tone_notes"]),
                    "translation_landmines": json.loads(row["landmines_json"] or "[]"),
                },
                ensure_ascii=False,
            )
        )
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return path


def _write_proper_nouns(
    base: Path, *, book_slug: str, entities: Sequence[Entity]
) -> Path:
    payload = {
        "version": 1,
        "generated_at": utc_now_iso(),
        "book_slug": book_slug,
        "names": [
            {
                "en": entity.en,
                "count": entity.count,
                "kind": entity.kind.value
                if hasattr(entity.kind, "value")
                else str(entity.kind),
            }
            for entity in entities
        ],
    }
    path = base / "artifacts" / "proper_nouns.yaml"
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return path


def _approve_brief(base: Path, book: Any, db: Database) -> AnalyzeResult:
    brief = load_brief_draft(base)
    if brief is None:
        if load_approved_brief(base) is not None:
            return AnalyzeResult(
                brief_path=base / "artifacts" / "brief.yaml", frozen=True
            )
        raise ConfigError(
            "no brief draft to approve",
            hint="run `shz analyze --draft-only` or `shz analyze --review-brief` first",
        )
    validate_brief(brief)
    path, digest = freeze_brief(base, book_slug=str(book["slug"]), brief=brief)
    db.set_book_brief_hash(int(book["id"]), digest)
    return AnalyzeResult(
        brief_path=path,
        draft_path=base / "artifacts" / "brief.draft.yaml",
        frozen=True,
    )


def run_analyze(
    *,
    root: Path,
    slug: str | None,
    settings: Settings,
    profile: ModelProfile,
    provider: Provider,
    approve: bool = False,
    draft_only: bool = False,
    review_brief: bool = False,
    only_chapter: int | None = None,
    auto_glossary: bool = False,
    sample_budget: int | None = None,
) -> AnalyzeResult:
    base = find_book_dir(root, slug, settings.project.books_dir)
    db = Database(base / "book.db")
    db.initialize()
    try:
        book = db.fetchone("SELECT * FROM books LIMIT 1")
        if book is None:
            raise ConfigError("the workspace has no book row", hint="re-run `shz init`")
        book_id = int(book["id"])
        if approve:
            return _approve_brief(base, book, db)

        if auto_glossary:
            _persist_glossary_mode(base, "auto")
        mode = "auto" if auto_glossary else settings.glossary.mode
        budget = sample_budget or settings.pipeline.analyze.sample_token_budget
        started_at = utc_now_iso()
        run_id = db.start_run(
            book_id=book_id,
            stage="analyze",
            config_json=json.dumps(redacted_config(settings), sort_keys=True, ensure_ascii=False),
        )
        run_dir = base / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        stats: dict[str, Any] = {
            "chapters": len(db.chapters(book_id)),
            "glossary_mode": mode,
        }
        result = AnalyzeResult(run_id=run_id, mode=mode)
        cache = ResponseCache(db, enabled=True)
        prompts = {prompt_id: load_prompt(prompt_id) for prompt_id in PROMPTS}
        models = {name: tier.model for name, tier in profile.tiers.items()}

        def finalize(status: str) -> None:
            calls = db.calls_for_run(run_id)
            cost = round(sum(float(row["cost_usd"] or 0.0) for row in calls), 8)
            cached = sum(1 for row in calls if int(row["cached"]))
            stats.update(
                calls=len(calls),
                cached_calls=cached,
                cost_usd=cost,
                cache_hit_rate=round(cached / max(1, len(calls)), 4),
            )
            db.update_run(run_id, stats=stats, cost_usd=cost)
            artifacts_hash = _artifact_hashes(base)
            db.update_run_artifacts(run_id, artifacts_hash)
            _write_calls(base, run_id, db)
            payload = {
                "run_id": run_id,
                "book_slug": str(book["slug"]),
                "stage": "analyze",
                "status": status,
                "started_at": started_at,
                "finished_at": utc_now_iso(),
                "config_snapshot": redacted_config(settings),
                "prompt_versions": {
                    prompt_id: prompt.version for prompt_id, prompt in prompts.items()
                },
                "artifacts_hash": artifacts_hash,
                "models": models,
                "stats": stats,
            }
            (run_dir / "manifest.json").write_text(
                json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            db.transition_run(run_id, status)
            result.cost_usd = cost
            result.calls = len(calls)
            result.cached_calls = cached

        try:
            segments = [
                segment
                for chapter in db.chapters(book_id)
                for segment in db.segments(chapter_id=int(chapter["id"]))
                if str(segment["source_text"]).strip()
            ]
            metrics = compute_style_metrics([str(segment["source_text"]) for segment in segments])
            db.set_book_source_metrics(book_id, metrics)
            stats["style_metrics"] = metrics

            sample = build_sample(db, book_id, budget=budget)
            stats["sample"] = sample.manifest

            brief = load_approved_brief(base)
            if brief is None:
                outcome = BriefAnalyst().run(
                    provider=provider,
                    tier=profile.tier(prompts["p01_brief"].tier),
                    db=db,
                    run_id=run_id,
                    cache=cache,
                    book_title=str(book["title_en"] or ""),
                    book_author=str(book["author"] or ""),
                    sample_text=sample.text,
                    style_metrics=metrics,
                )
                result.draft_path = write_brief_draft(
                    base, book_slug=str(book["slug"]), brief=outcome.brief
                )
                result.brief_path = result.draft_path
                stats["brief"] = "fallback" if outcome.used_fallback else "draft"
                if draft_only:
                    finalize("completed")
                    return result
                if review_brief or settings.pipeline.analyze.brief_approval == "manual":
                    result.paused_for_brief = True
                    finalize("paused")
                    return result
                path, digest = freeze_brief(
                    base, book_slug=str(book["slug"]), brief=outcome.brief
                )
                result.brief_path = path
                result.frozen = True
                db.set_book_brief_hash(book_id, digest)
                stats["brief_hash"] = digest
                brief = outcome.brief
            else:
                result.brief_path = base / "artifacts" / "brief.yaml"
                result.frozen = True
                stats["brief"] = "existing"
                stats["brief_hash"] = brief_hash(brief)

            if draft_only:
                finalize("completed")
                return result

            if mode == "provided":
                result.glossary_entries = import_provided_glossary(
                    db, book_id=book_id, settings=settings, root=root, base=base
                )
                write_glossary_artifact(
                    db, book_id=book_id, base=base, book_slug=str(book["slug"])
                )
                stats["glossary_entries"] = result.glossary_entries

            chapters = db.chapters(book_id)
            if only_chapter is not None:
                chapter = db.get_chapter_by_idx(book_id, only_chapter)
                if chapter is None:
                    raise ConfigError(f"chapter {only_chapter} does not exist")
                chapters = [chapter]

            summarizer = ChapterSummarizer()
            summary_tier = profile.tier(settings.pipeline.analyze.summary_model_tier)
            for chapter in chapters:
                chapter_segments = db.segments(chapter_id=int(chapter["id"]))
                summary_result = summarizer.summarize(
                    provider=provider,
                    tier=summary_tier,
                    db=db,
                    run_id=run_id,
                    cache=cache,
                    chapter_index=int(chapter["idx"]),
                    chapter_title=str(chapter["title_en"] or ""),
                    segments=chapter_segments,
                )
                if summary_result is None:
                    continue
                summary = summary_result.value
                db.upsert_chapter_summary(
                    chapter_id=int(chapter["id"]),
                    summary_en=summary.summary_en,
                    continuity=summary.continuity.model_dump(),
                    tone_notes=summary.tone_notes,
                    translation_landmines=summary.translation_landmines,
                )
                if chapter["status"] == ChapterStatus.PENDING:
                    db.transition_chapter(int(chapter["id"]), ChapterStatus.ANALYZED)
                result.summaries += 1
            _write_summaries(base, db, book_id)
            stats["summaries"] = result.summaries

            scope_segments = [
                segment
                for chapter in chapters
                for segment in db.segments(chapter_id=int(chapter["id"]))
                if str(segment["source_text"]).strip()
            ]
            extractor = EntityExtractor()
            entities = extractor.extract(
                provider=provider,
                tier=profile.tier(prompts["p03_entities"].tier),
                db=db,
                run_id=run_id,
                cache=cache,
                segments=scope_segments,
                use_llm=mode == "auto",
            )
            result.entities = len(entities)
            stats["entities"] = result.entities

            if mode == "auto":
                existing_rows = db.glossary_entries(book_id)
                batch = GlossaryProposer().propose(
                    provider=provider,
                    tier=profile.tier(prompts["p04_glossary"].tier),
                    db=db,
                    run_id=run_id,
                    cache=cache,
                    entities=entities,
                    brief=brief,
                    existing_rows=existing_rows,
                )
                result.proposals = len(batch.proposals)
                approved = 0
                for proposal in batch.proposals:
                    status = "proposed"
                    if (
                        settings.glossary.approval == "auto"
                        and proposal.confidence
                        >= settings.glossary.auto_approve_min_confidence
                    ):
                        status = "approved"
                        approved += 1
                    db.upsert_glossary_entry(
                        book_id=book_id,
                        term_en=proposal.en,
                        term_fa=proposal.fa,
                        kind=proposal.kind,
                        status=status,
                        source="candidate",
                        confidence=proposal.confidence,
                        aliases_fa=proposal.aliases_fa,
                        gender=proposal.gender,
                        notes=proposal.notes,
                        approved_by="auto" if status == "approved" else None,
                        preserve_approved=True,
                    )
                rows = db.glossary_entries(book_id)
                _candidate_yaml(
                    base,
                    book_slug=str(book["slug"]),
                    entities=entities,
                    proposals=batch.proposals,
                    rows=rows,
                )
                write_glossary_artifact(
                    db, book_id=book_id, base=base, book_slug=str(book["slug"])
                )
                result.glossary_entries = len(rows)
                stats["proposals"] = result.proposals
                stats["approved_glossary"] = len(
                    [row for row in rows if row["status"] == "approved"]
                )
                stats["auto_approved"] = approved
            elif mode == "off":
                _write_proper_nouns(base, book_slug=str(book["slug"]), entities=entities)
                result.proper_nouns = len(entities)
                stats["proper_nouns"] = result.proper_nouns

            finalize("completed")
            return result
        except Exception:
            finalize("failed")
            raise
    finally:
        db.close()
