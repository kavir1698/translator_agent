"""A5 Translator: one window -> validated TranslatorOutput (plan 6.5, P05)."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from tarjoman.context.packer import render_segments, segment_markup
from tarjoman.core.cost import TierProfile
from tarjoman.core.db import Database
from tarjoman.core.errors import SchemaValidationError
from tarjoman.core.models import TranslatorOutput
from tarjoman.llm.cache import ResponseCache
from tarjoman.llm.promptlib import load as load_prompt
from tarjoman.llm.provider import Provider
from tarjoman.persian.markup import MarkupProblem, validate_markup

from .base import AgentResult, run_agent

PROMPT_ID = "p05_translator"
_PERSIAN = re.compile(r"[\u0600-\u06FF]")
_STRICT_REMINDER = (
    "Your previous output was rejected because it lost or corrupted inline sentinel tokens "
    "or segment ids. Return every requested segment exactly once and copy every sentinel "
    "token exactly as it appears (⟨n⟩ … ⟨/n⟩, or ⟨n⟩ alone for an atomic element)."
)


@dataclass
class TranslationOutcome:
    result: AgentResult[TranslatorOutput]
    segments: list[dict[str, Any]]
    markup_problems: dict[str, list[MarkupProblem]] = field(default_factory=dict)
    strict_retry: bool = False


def max_output_tokens(source_tokens: int, tier: TierProfile, fa_ratio: float = 3.0) -> int:
    projected = math.ceil(max(1, source_tokens) * fa_ratio * 1.3)
    return max(512, min(projected, max(512, tier.max_output_tokens - 512)))


def _nodes(segment: Any) -> list[dict[str, Any]]:
    raw = segment["nodes_json"] if hasattr(segment, "keys") else segment.get("nodes_json", "[]")
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "[]")
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
    return list(raw or [])


class Translator:
    prompt_id = PROMPT_ID

    def __init__(self) -> None:
        self.prompt = load_prompt(self.prompt_id)

    def _problems(
        self, value: TranslatorOutput, segments: Sequence[Any]
    ) -> tuple[dict[str, list[MarkupProblem]], bool]:
        by_id = {str(segment["id"]): segment for segment in segments}
        problems: dict[str, list[MarkupProblem]] = {}
        valid = True
        for item in value.segments:
            segment = by_id.get(item.id)
            if segment is None:
                valid = False
                continue
            found = validate_markup(item.fa_markup, _nodes(segment))
            if not _PERSIAN.search(item.fa_markup):
                found = [*found, MarkupProblem("no_persian", "translation has no Persian letters")]
            if found:
                problems[item.id] = found
                valid = False
        return problems, valid

    def _valid(self, value: TranslatorOutput, segments: Sequence[Any]) -> bool:
        expected = {str(segment["id"]) for segment in segments}
        returned = [item.id for item in value.segments]
        if len(returned) != len(set(returned)) or set(returned) != expected:
            return False
        problems, valid = self._problems(value, segments)
        return valid and not problems

    def _valid_one(self, value: TranslatorOutput, *, segment: Any) -> bool:
        return self._valid(value, [segment])

    def translate(
        self,
        *,
        provider: Provider,
        tier: TierProfile,
        db: Database,
        run_id: str | None,
        cache: ResponseCache | None,
        variables: dict[str, str],
        segments: Sequence[Any],
        extra_instruction: str = "",
    ) -> TranslationOutcome:
        source_tokens = sum(max(1, int(segment["tokens_est"])) for segment in segments)
        variables = dict(variables)
        if extra_instruction:
            variables["window_instruction"] = (
                f"{variables.get('window_instruction', '')} {extra_instruction}".strip()
            )
        try:
            result = run_agent(
                provider=provider,
                prompt=self.prompt,
                schema_model=TranslatorOutput,
                variables=variables,
                tier=tier,
                db=db,
                run_id=run_id,
                cache=cache,
                max_output_tokens=max_output_tokens(source_tokens, tier),
                cache_if=lambda value: self._valid(value, segments),
            )
        except SchemaValidationError:
            return self._per_segment(
                provider=provider,
                tier=tier,
                db=db,
                run_id=run_id,
                cache=cache,
                variables=variables,
                segments=segments,
            )
        if result.finish_reason == "length":
            return self._per_segment(
                provider=provider,
                tier=tier,
                db=db,
                run_id=run_id,
                cache=cache,
                variables=variables,
                segments=segments,
            )
        expected = {str(segment["id"]) for segment in segments}
        returned = [item.id for item in result.value.segments]
        partial = set(returned) != expected
        problems, valid = self._problems(result.value, segments)
        if partial or not valid:
            strict_vars = dict(variables)
            strict_vars["window_instruction"] = (
                f"{variables.get('window_instruction', '')} {_STRICT_REMINDER}"
            ).strip()
            retry = run_agent(
                provider=provider,
                prompt=self.prompt,
                schema_model=TranslatorOutput,
                variables=strict_vars,
                tier=tier,
                db=db,
                run_id=run_id,
                cache=None,
                max_output_tokens=max_output_tokens(source_tokens, tier),
            )
            if self._valid(retry.value, segments):
                return TranslationOutcome(
                    result=retry,
                    segments=[item.model_dump() for item in retry.value.segments],
                    strict_retry=True,
                )
            merged = {item.id: item for item in result.value.segments}
            merged.update({item.id: item for item in retry.value.segments})
            ordered_ids = [str(segment["id"]) for segment in segments]
            value = TranslatorOutput(
                segments=[merged[segment_id] for segment_id in ordered_ids if segment_id in merged],
                glossary_suggestions=retry.value.glossary_suggestions
                or result.value.glossary_suggestions,
                window_notes=retry.value.window_notes or result.value.window_notes,
            )
            retry = AgentResult(
                value=value,
                prompt_hash=retry.prompt_hash,
                model=retry.model,
                text=retry.text,
                tokens_in=result.tokens_in + retry.tokens_in,
                tokens_out=result.tokens_out + retry.tokens_out,
                cost_usd=result.cost_usd + retry.cost_usd,
                cached=result.cached and retry.cached,
                latency_ms=result.latency_ms + retry.latency_ms,
                finish_reason=retry.finish_reason,
            )
            problems, _ = self._problems(value, segments)
            return TranslationOutcome(
                result=retry,
                segments=[item.model_dump() for item in value.segments],
                markup_problems=problems,
                strict_retry=True,
            )
        return TranslationOutcome(
            result=result,
            segments=[item.model_dump() for item in result.value.segments],
            markup_problems=problems,
        )

    def _per_segment(
        self,
        *,
        provider: Provider,
        tier: TierProfile,
        db: Database,
        run_id: str | None,
        cache: ResponseCache | None,
        variables: dict[str, str],
        segments: Sequence[Any],
    ) -> TranslationOutcome:
        """Last-resort single-segment calls; truncated or oversized windows never persist."""
        collected: list[dict[str, Any]] = []
        problems: dict[str, list[MarkupProblem]] = {}
        tokens_in = tokens_out = latency_ms = 0
        cost = 0.0
        cached_all = True
        prompt_hash = ""
        model = tier.model
        for segment in segments:
            subset = dict(variables)
            subset["segments"] = render_segments([segment])
            try:
                result = run_agent(
                    provider=provider,
                    prompt=self.prompt,
                    schema_model=TranslatorOutput,
                    variables=subset,
                    tier=tier,
                    db=db,
                    run_id=run_id,
                    cache=cache,
                    max_output_tokens=max_output_tokens(
                        max(1, int(segment["tokens_est"])), tier
                    ),
                    cache_if=partial(self._valid_one, segment=segment),
                )
            except SchemaValidationError:
                continue
            tokens_in += result.tokens_in
            tokens_out += result.tokens_out
            cost += result.cost_usd
            latency_ms += result.latency_ms
            cached_all = cached_all and result.cached
            prompt_hash = result.prompt_hash
            found, _ = self._problems(result.value, [segment])
            problems.update(found)
            collected.extend(item.model_dump() for item in result.value.segments)
        if not collected:
            raise SchemaValidationError("per-segment fallback produced no translations")
        combined = AgentResult(
            value=TranslatorOutput(segments=[], glossary_suggestions=[], window_notes=""),
            prompt_hash=prompt_hash,
            model=model,
            text="",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
            cached=cached_all,
            latency_ms=latency_ms,
            finish_reason="stop",
        )
        return TranslationOutcome(
            result=combined,
            segments=collected,
            markup_problems=problems,
            strict_retry=True,
        )


def source_markup(segment: Any) -> str:
    return segment_markup(segment)


def refuse_if_no_segments(value: TranslatorOutput) -> None:
    if not value.segments:
        raise SchemaValidationError("translator returned no segments")
