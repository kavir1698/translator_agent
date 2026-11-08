"""The single provider contract every LLM call goes through."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(slots=True)
class Message:
    role: str
    content: str


@dataclass(slots=True)
class LLMResult:
    text: str
    parsed: dict[str, Any] | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    finish_reason: str | None = None
    provider_request_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Provider(Protocol):
    def complete(
        self,
        *,
        messages: list[Message],
        model: str,
        temperature: float | None,
        max_output_tokens: int,
        response_schema: dict[str, Any] | None = None,
        seed: int | None = None,
        thinking: str | None = None,
    ) -> LLMResult: ...


def messages_json(messages: list[Message]) -> str:
    return "\n".join(f"{message.role}:{message.content}" for message in messages)
