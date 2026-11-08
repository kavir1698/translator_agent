"""Scripted, network-free provider for the entire test suite."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from shahrzad.core.errors import MissingFixture, TransientProviderError
from shahrzad.core.ids import sha256_text
from shahrzad.core.tokens import estimate_tokens_en

from .provider import LLMResult, Message, messages_json
from .schema import extract_json_object


class MockProviderFailure(TransientProviderError):
    """An injected failure mode from a fixture (invalid_json, rate_limit, ...)."""

    def __init__(self, mode: str) -> None:
        super().__init__(f"mock provider injected failure: {mode}")
        self.mode = mode


def fixture_key(
    *,
    model: str,
    temperature: float | None,
    messages: list[Message],
    response_schema: dict[str, Any] | None = None,
) -> str:
    schema_part = sha256_text(json.dumps(response_schema, sort_keys=True)) if response_schema else "-"
    rendered = messages_json(messages)
    return sha256_text(f"{model}|{temperature}|{rendered}|{schema_part}")[:16]


def _estimate_tokens(text: str) -> int:
    return estimate_tokens_en(text)


class MockProvider:
    def __init__(
        self,
        *,
        fixtures_dir: str | Path | None = None,
        script: list[dict[str, Any] | LLMResult] | None = None,
    ) -> None:
        self.fixtures_dir = Path(fixtures_dir) if fixtures_dir else None
        self._script = list(script or [])
        self.calls: list[dict[str, Any]] = []
        self._fail_counts: dict[str, int] = {}

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
    ) -> LLMResult:
        key = fixture_key(
            model=model,
            temperature=temperature,
            messages=messages,
            response_schema=response_schema,
        )
        self.calls.append(
            {"key": key, "model": model, "temperature": temperature, "messages": messages}
        )
        fixture = self._load_fixture(key)
        if fixture is None:
            if self._script:
                return self._from_payload(self._script.pop(0), model, messages)
            raise MissingFixture(
                f"MockProvider has no fixture for request {key}",
                hint="record the fixture or add a scripted response",
            )

        remaining = self._fail_counts.get(key, int(fixture.get("fail_times", 0)))
        if remaining > 0:
            self._fail_counts[key] = remaining - 1
            failure = str(fixture.get("failure", "invalid_json"))
            if failure == "markup_broken":
                responses = fixture.get("responses") or []
                if not responses:
                    raise MissingFixture(f"fixture {key} has no responses")
                return self._from_payload(_break_markup(responses[0]), model, messages)
            raise MockProviderFailure(failure)

        responses = fixture.get("responses") or []
        if not responses:
            raise MissingFixture(f"fixture {key} has no responses")
        return self._from_payload(responses[0], model, messages)

    def _load_fixture(self, key: str) -> dict[str, Any] | None:
        if self.fixtures_dir is None or not self.fixtures_dir.exists():
            return None
        direct = self.fixtures_dir / f"{key}.json"
        candidates = [direct] if direct.is_file() else sorted(self.fixtures_dir.glob(f"*/{key}.json"))
        if not candidates:
            return None
        return json.loads(candidates[0].read_text(encoding="utf-8"))

    def _from_payload(
        self, payload: dict[str, Any] | LLMResult, model: str, messages: list[Message]
    ) -> LLMResult:
        if isinstance(payload, LLMResult):
            return payload
        text = json.dumps(payload, ensure_ascii=False)
        try:
            parsed = extract_json_object(text)
        except Exception:
            parsed = None
        return LLMResult(
            text=text,
            parsed=parsed,
            tokens_in=_estimate_tokens(messages_json(messages)),
            tokens_out=_estimate_tokens(text),
            cost_usd=0.0,
            latency_ms=0,
            finish_reason="stop",
            meta={"mock": True, "model": model},
        )


def _break_markup(payload: Any) -> Any:
    """Inject the `markup_broken` failure: strip every sentinel from translations."""
    if not isinstance(payload, dict):
        return payload
    broken = dict(payload)
    segments = broken.get("segments")
    if isinstance(segments, list):
        broken["segments"] = [
            {
                **item,
                "fa_markup": re.sub(
                    r"⟨/?\d+⟩|⟦/?\d+⟧|\[\[#?\d+\]\]|\[\[/\d+\]\]",
                    "",
                    str(item.get("fa_markup", "")),
                ),
            }
            if isinstance(item, dict)
            else item
            for item in segments
        ]
    return broken
