"""Shared agent runner: render, cache, call, validate, repair, record (plan 9.2-9.3)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from shahrzad.core.cost import TierProfile
from shahrzad.core.db import Database
from shahrzad.core.errors import SchemaValidationError
from shahrzad.core.ids import sha256_text
from shahrzad.llm.cache import ResponseCache, cache_key
from shahrzad.llm.promptlib import Prompt
from shahrzad.llm.provider import LLMResult, Message, Provider, messages_json
from shahrzad.llm.schema import build_response_schema, parse_model

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class AgentSpec:
    id: str
    prompt_id: str
    agent: str
    schema: type[BaseModel]


@dataclass(frozen=True)
class AgentResult[ModelT: BaseModel]:
    value: ModelT
    prompt_hash: str
    model: str
    text: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    cached: bool
    latency_ms: int
    finish_reason: str | None


def _coerce(schema_model: type[ModelT], result: LLMResult) -> ModelT:
    errors: list[str] = []
    if result.parsed is not None:
        try:
            return schema_model.model_validate(result.parsed)
        except ValidationError as exc:
            errors.append(str(exc))
    try:
        return parse_model(schema_model, result.text)
    except SchemaValidationError as exc:
        errors.append(str(exc))
    raise SchemaValidationError(
        f"{schema_model.__name__} validation failed:\n" + "\n".join(errors)
    )


def _record(
    db: Database,
    *,
    run_id: str | None,
    agent: str,
    model: str,
    prompt_hash: str,
    messages: list[Message],
    result: LLMResult,
    cached: bool,
    error: str | None = None,
) -> None:
    db.record_llm_call(
        run_id_value=run_id,
        agent=agent,
        model=model,
        prompt_hash=prompt_hash,
        input_hash=sha256_text(messages_json(messages)),
        cached=cached,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=0.0 if cached else result.cost_usd,
        latency_ms=0 if cached else result.latency_ms,
        finish_reason=result.finish_reason,
        error=error,
    )


def run_agent(
    *,
    provider: Provider,
    prompt: Prompt,
    schema_model: type[ModelT],
    variables: dict[str, Any],
    tier: TierProfile,
    db: Database,
    run_id: str | None,
    cache: ResponseCache | None = None,
    max_output_tokens: int | None = None,
    repair: bool = True,
    post_validate: Callable[[ModelT], None] | None = None,
    cache_if: Callable[[ModelT], bool] | None = None,
) -> AgentResult[ModelT]:
    """One structured agent call. Cache first, then one schema-repair call."""
    rendered = prompt.render(variables)
    prompt_hash = prompt.hash(rendered)
    messages = [Message(role="user", content=rendered)]
    response_schema = build_response_schema(schema_model)
    temperature = prompt.temperature if prompt.temperature is not None else tier.temperature
    output_tokens = max_output_tokens or tier.max_output_tokens

    if cache is not None:
        key = cache_key(
            model=tier.model,
            temperature=temperature,
            response_schema=response_schema,
            prompt_hash=prompt_hash,
            messages=messages,
        )
        hit = cache.get(key)
        if hit is not None:
            try:
                value = _coerce(schema_model, hit)
                if post_validate is not None:
                    post_validate(value)
            except SchemaValidationError:
                hit = None
            else:
                _record(
                    db,
                    run_id=run_id,
                    agent=prompt.agent,
                    model=tier.model,
                    prompt_hash=prompt_hash,
                    messages=messages,
                    result=hit,
                    cached=True,
                )
                return AgentResult(
                    value=value,
                    prompt_hash=prompt_hash,
                    model=tier.model,
                    text=hit.text,
                    tokens_in=hit.tokens_in,
                    tokens_out=hit.tokens_out,
                    cost_usd=0.0,
                    cached=True,
                    latency_ms=0,
                    finish_reason=hit.finish_reason,
                )
    else:
        key = None

    result = provider.complete(
        messages=messages,
        model=tier.model,
        temperature=temperature,
        max_output_tokens=output_tokens,
        response_schema=response_schema,
    )
    _record(
        db,
        run_id=run_id,
        agent=prompt.agent,
        model=tier.model,
        prompt_hash=prompt_hash,
        messages=messages,
        result=result,
        cached=False,
        error="output truncated" if result.finish_reason == "length" else None,
    )
    if result.finish_reason == "length":
        output_tokens = min(
            max(output_tokens * 2, 1024), max(512, tier.max_output_tokens)
        )
        result = provider.complete(
            messages=messages,
            model=tier.model,
            temperature=temperature,
            max_output_tokens=output_tokens,
            response_schema=response_schema,
        )
        _record(
            db,
            run_id=run_id,
            agent=prompt.agent,
            model=tier.model,
            prompt_hash=prompt_hash,
            messages=messages,
            result=result,
            cached=False,
            error="output truncated" if result.finish_reason == "length" else None,
        )

    try:
        value = _coerce(schema_model, result)
    except SchemaValidationError as exc:
        if not repair:
            raise
        repair_messages = [
            *messages,
            Message(role="assistant", content=result.text),
            Message(
                role="user",
                content=(
                    f"Your previous output failed validation: {exc}. "
                    f"Return only corrected JSON matching the {schema_model.__name__} schema. "
                    "No markdown fences, no prose, no extra fields."
                ),
            ),
        ]
        result = provider.complete(
            messages=repair_messages,
            model=tier.model,
            temperature=temperature,
            max_output_tokens=output_tokens,
            response_schema=response_schema,
        )
        _record(
            db,
            run_id=run_id,
            agent=prompt.agent,
            model=tier.model,
            prompt_hash=f"{prompt_hash}:repair",
            messages=repair_messages,
            result=result,
            cached=False,
            error=str(exc)[:500],
        )
        value = _coerce(schema_model, result)

    if post_validate is not None:
        post_validate(value)

    if cache is not None and key is not None and (cache_if is None or cache_if(value)):
        cache.put(key, result)

    return AgentResult(
        value=value,
        prompt_hash=prompt_hash,
        model=tier.model,
        text=result.text,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        cached=False,
        latency_ms=result.latency_ms,
        finish_reason=result.finish_reason,
    )
