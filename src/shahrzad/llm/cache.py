"""SQLite-backed response cache."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from shahrzad.core.db import Database
from shahrzad.core.ids import sha256_text

from .provider import LLMResult, Message, messages_json
from .schema import schema_hash


def cache_key(
    *,
    model: str,
    temperature: float | None,
    response_schema: dict[str, Any] | None,
    prompt_hash: str,
    messages: list[Message],
) -> str:
    schema_part = sha256_text(json.dumps(response_schema, sort_keys=True)) if response_schema else "-"
    return sha256_text(
        f"{model}|{temperature}|{schema_part}|{prompt_hash}|{messages_json(messages)}"
    )


class ResponseCache:
    def __init__(
        self, db: Database, *, enabled: bool = True, write_enabled: bool = True
    ) -> None:
        self.db = db
        self.enabled = enabled
        self.write_enabled = write_enabled

    def get(self, key: str) -> LLMResult | None:
        if not self.enabled:
            return None
        raw = self.db.cache_get(key)
        if raw is None:
            return None
        data = json.loads(raw)
        return LLMResult(
            text=data["text"],
            parsed=data.get("parsed"),
            tokens_in=int(data.get("tokens_in", 0)),
            tokens_out=int(data.get("tokens_out", 0)),
            cached_tokens=int(data.get("tokens_in", 0)),
            cost_usd=0.0,
            latency_ms=0,
            finish_reason="cache",
            meta={"cached": True},
        )

    def put(self, key: str, result: LLMResult) -> None:
        if not self.enabled or not self.write_enabled:
            return
        payload = asdict(result)
        payload.pop("meta", None)
        payload["cached_tokens"] = 0
        payload["cost_usd"] = 0.0
        payload["latency_ms"] = 0
        self.db.cache_put(key, json.dumps(payload, ensure_ascii=False))


def static_schema_hash(schema: dict[str, Any] | None) -> str:
    return schema_hash(schema) if schema else "-"
