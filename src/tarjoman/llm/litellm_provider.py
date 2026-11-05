"""Real provider via LiteLLM. The only module that imports litellm."""

from __future__ import annotations

import os
import time
from typing import Any

from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from tarjoman.core.config import api_key_for
from tarjoman.core.cost import ModelProfile, PriceTable, TierProfile, call_cost_usd, is_peak
from tarjoman.core.errors import ProviderError, TransientProviderError

from .provider import LLMResult, Message, messages_json
from .schema import extract_json_object

_TRANSIENT_STATUS = {408, 409, 425, 429}


def _is_transient(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status in _TRANSIENT_STATUS or status >= 500):
        return True
    name = type(exc).__name__.lower()
    return any(token in name for token in ("timeout", "connection", "ratelimit", "internalserver"))


def _before_sleep(state: RetryCallState) -> None:
    return None


class LiteLLMProvider:
    def __init__(
        self,
        *,
        profile: ModelProfile,
        api_key: str | None = None,
        timeout_s: int = 180,
        max_retries: int = 4,
        thinking: str | None = None,
    ) -> None:
        self.profile = profile
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.thinking = thinking or profile.thinking
        self._tier_by_model: dict[str, TierProfile] = {
            tier.model: tier for tier in profile.tiers.values()
        }

    def _tier(self, model: str) -> TierProfile:
        return self._tier_by_model.get(model, self.profile.tiers["balanced"])

    def _pricing(self, model: str) -> PriceTable:
        return self._tier(model).pricing

    def _model_string(self, model: str) -> str:
        if "/" in model:
            return model
        return f"{self.profile.provider_for(self._tier(model))}/{model}"

    def _api_base(self, model: str) -> str | None:
        return self.profile.base_url_for(self._tier(model)) or None

    def _api_key(self, model: str) -> str | None:
        tier = self._tier(model)
        if tier.api_key_env:
            value = os.environ.get(tier.api_key_env)
            if value:
                return value
        return api_key_for(self.profile.provider_for(tier)) or self.api_key

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
        import litellm

        thinking_mode = thinking or self.thinking
        kwargs: dict[str, Any] = {
            "model": self._model_string(model),
            "messages": [{"role": item.role, "content": item.content} for item in messages],
            "max_tokens": max_output_tokens,
            "timeout": self.timeout_s,
            "api_base": self._api_base(model),
            "extra_body": {"thinking": {"type": thinking_mode}},
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if response_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}
        if seed is not None:
            kwargs["seed"] = seed
        api_key = self._api_key(model)
        if api_key:
            kwargs["api_key"] = api_key

        attempts = {"value": 0}

        def _attempt() -> Any:
            attempts["value"] += 1
            return litellm.completion(**kwargs)

        retrying = retry(
            retry=retry_if_exception(_is_transient),
            wait=wait_exponential_jitter(initial=1, max=60, jitter=1),
            stop=stop_after_attempt(self.max_retries + 1),
            before_sleep=_before_sleep,
            reraise=True,
        )(_attempt)

        started = time.perf_counter()
        try:
            response = retrying()
        except Exception as exc:
            if _is_transient(exc):
                raise TransientProviderError(f"provider call failed after retries: {exc}") from exc
            raise ProviderError(f"provider call failed: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        choice = response.choices[0]
        text = choice.message.content or ""
        usage = getattr(response, "usage", None)
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0)
        details = getattr(usage, "prompt_tokens_details", None)
        cached_tokens = int(getattr(details, "cached_tokens", 0) or 0)
        finish_reason = getattr(choice, "finish_reason", None)
        parsed = None
        if response_schema is not None and text:
            try:
                parsed = extract_json_object(text)
            except Exception:
                parsed = None

        cost = 0.0
        try:
            cost = float(litellm.completion_cost(completion_response=response) or 0.0)
        except Exception:
            cost = call_cost_usd(
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                pricing=self._pricing(model),
                cached_tokens=cached_tokens,
                peak=is_peak(),
            )

        return LLMResult(
            text=text,
            parsed=parsed,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cached_tokens=cached_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            provider_request_id=str(getattr(response, "id", "") or "") or None,
            meta={"attempts": attempts["value"], "messages": messages_json(messages)},
        )
