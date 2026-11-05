"""A2 ChapterSummarizer: one continuity summary per chapter (plan 6.1.3)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from tarjoman.core.cost import TierProfile
from tarjoman.core.db import Database
from tarjoman.core.errors import SchemaValidationError
from tarjoman.core.models import ChapterSummary, ContinuityFacts
from tarjoman.llm.cache import ResponseCache
from tarjoman.llm.promptlib import load as load_prompt
from tarjoman.llm.provider import Provider

from .base import AgentResult, run_agent

AGENT = "A2"
PROMPT_ID = "p02_summarizer"
CHUNK_TOKENS = 8000


def _chunks(
    segments: Sequence[Any], max_tokens: int = CHUNK_TOKENS
) -> list[list[Any]]:
    total = sum(max(1, int(segment["tokens_est"])) for segment in segments)
    if total <= max_tokens or len(segments) < 2:
        return [list(segments)]
    target = total / 2
    first: list[Any] = []
    running = 0
    for index, segment in enumerate(segments):
        tokens = max(1, int(segment["tokens_est"]))
        if running + tokens > target and first and index > 0:
            return [first, list(segments[index:])]
        first.append(segment)
        running += tokens
    return [first]


def _truncate_summary(text: str, limit: int = 2000) -> str:
    if len(text) <= limit:
        return text
    cut = text.rfind(". ", 0, limit)
    if cut < 200:
        cut = limit
    return text[:cut].strip()


def _merge_text(parts: list[ChapterSummary]) -> str:
    blocks = []
    for index, part in enumerate(parts, start=1):
        blocks.append(
            f"PART {index} SUMMARY:\n{part.summary_en}\n"
            f"CONTINUITY: {json.dumps(part.continuity.model_dump(), ensure_ascii=False)}\n"
            f"TONE: {part.tone_notes}\n"
            f"LANDMINES: {json.dumps(part.translation_landmines, ensure_ascii=False)}"
        )
    return "\n\n".join(blocks)


def _merge(parts: list[ChapterSummary]) -> ChapterSummary:
    if len(parts) == 1:
        return parts[0]
    summary = _truncate_summary(" ".join(part.summary_en for part in parts))

    def union(values: list[str]) -> list[str]:
        seen: dict[str, None] = {}
        for value in values:
            seen.setdefault(value, None)
        return list(seen)

    continuity = ContinuityFacts(
        characters=union([item for part in parts for item in part.continuity.characters]),
        relationships=union(
            [item for part in parts for item in part.continuity.relationships]
        ),
        places_and_objects=union(
            [item for part in parts for item in part.continuity.places_and_objects]
        ),
        time=union([item for part in parts for item in part.continuity.time]),
        open_threads=union([item for part in parts for item in part.continuity.open_threads]),
    )
    return ChapterSummary(
        summary_en=summary,
        continuity=continuity,
        tone_notes=" | ".join(part.tone_notes for part in parts if part.tone_notes),
        translation_landmines=union(
            [item for part in parts for item in part.translation_landmines]
        )[:3],
    )


class ChapterSummarizer:
    prompt_id = PROMPT_ID
    agent = AGENT

    def summarize(
        self,
        *,
        provider: Provider,
        tier: TierProfile,
        db: Database,
        run_id: str | None,
        cache: ResponseCache | None,
        chapter_index: int,
        chapter_title: str,
        segments: Sequence[Any],
    ) -> AgentResult[ChapterSummary] | None:
        usable = [segment for segment in segments if str(segment["source_text"]).strip()]
        if not usable:
            return None
        prompt = load_prompt(self.prompt_id)
        chunked = _chunks(usable)
        parts: list[ChapterSummary] = []
        last: AgentResult[ChapterSummary] | None = None
        for index, chunk in enumerate(chunked, start=1):
            title = chapter_title or f"Chapter {chapter_index}"
            label: int | str = chapter_index if len(chunked) == 1 else f"{chapter_index} part {index}/{len(chunked)}"
            source_text = "\n\n".join(str(segment["source_text"]) for segment in chunk)
            last = run_agent(
                provider=provider,
                prompt=prompt,
                schema_model=ChapterSummary,
                variables={
                    "chapter_index": label,
                    "chapter_title": title,
                    "source_text": source_text,
                },
                tier=tier,
                db=db,
                run_id=run_id,
                cache=cache,
            )
            parts.append(last.value)
        if last is None:
            return None
        if len(parts) > 1:
            try:
                merged_result = run_agent(
                    provider=provider,
                    prompt=prompt,
                    schema_model=ChapterSummary,
                    variables={
                        "chapter_index": f"{chapter_index} (merge)",
                        "chapter_title": chapter_title or f"Chapter {chapter_index}",
                        "source_text": _merge_text(parts),
                    },
                    tier=tier,
                    db=db,
                    run_id=run_id,
                    cache=cache,
                )
            except SchemaValidationError:
                merged = _merge(parts)
            else:
                merged = merged_result.value
                last = merged_result
        else:
            merged = parts[0]
        return AgentResult(
            value=merged,
            prompt_hash=last.prompt_hash,
            model=last.model,
            text=last.text,
            tokens_in=last.tokens_in,
            tokens_out=last.tokens_out,
            cost_usd=last.cost_usd,
            cached=last.cached,
            latency_ms=last.latency_ms,
            finish_reason=last.finish_reason,
        )
