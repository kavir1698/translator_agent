"""Price tables, peak/off-peak tariffs, cost accounting and book estimates."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from .errors import ConfigError

CostMode = Literal["auto", "economy", "standard", "quality"]
Tier = Literal["quality", "balanced", "cheap"]


def is_peak(moment: datetime | None = None) -> bool:
    """DeepSeek peak: 01:00-04:00 and 06:00-10:00 UTC, Monday-Friday."""
    now = (moment or datetime.now(UTC)).astimezone(UTC)
    if now.weekday() >= 5:
        return False
    return 1 <= now.hour < 4 or 6 <= now.hour < 10


@dataclass(frozen=True)
class PriceTable:
    cache_hit_input: float
    input: float
    output: float

    def scaled(self, factor: float) -> PriceTable:
        return PriceTable(
            cache_hit_input=self.cache_hit_input * factor,
            input=self.input * factor,
            output=self.output * factor,
        )


@dataclass(frozen=True)
class TierProfile:
    model: str
    temperature: float | None
    max_output_tokens: int
    pricing: PriceTable
    provider: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None

    @property
    def effective_temperature(self) -> float | None:
        return self.temperature


@dataclass(frozen=True)
class ModelProfile:
    provider: str
    base_url: str
    tiers: dict[str, TierProfile]
    thinking: str = "disabled"
    raw: dict[str, Any] = field(default_factory=dict)

    def tier(self, name: str) -> TierProfile:
        try:
            return self.tiers[name]
        except KeyError as exc:
            raise ConfigError(
                f"model profile has no tier {name!r} (has: {', '.join(self.tiers)})"
            ) from exc

    def provider_for(self, tier: TierProfile) -> str:
        return tier.provider or self.provider

    def base_url_for(self, tier: TierProfile) -> str:
        return tier.base_url or self.base_url


def _price_table(raw: dict[str, Any] | None) -> PriceTable:
    if not raw:
        raise ConfigError("model profile is missing pricing_usd_per_mtok")
    return PriceTable(
        cache_hit_input=float(raw.get("cache_hit_input", 0.0)),
        input=float(raw["input"]),
        output=float(raw["output"]),
    )


def load_model_profile(path: str | Path) -> ModelProfile:
    file = Path(path)
    if not file.is_file():
        raise ConfigError(
            f"model profile not found: {file}",
            hint="check provider.models in configs/default.yaml",
        )
    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"model profile {file} is not a mapping")
    default_pricing = data.get("pricing_usd_per_mtok")
    tiers: dict[str, TierProfile] = {}
    for name in ("quality", "balanced", "cheap"):
        entry = data.get(name)
        if not entry:
            continue
        tiers[name] = TierProfile(
            model=str(entry["model"]),
            temperature=entry.get("temperature"),
            max_output_tokens=int(entry.get("max_output_tokens", 16384)),
            pricing=_price_table(entry.get("pricing_usd_per_mtok") or default_pricing),
            provider=str(entry["provider"]) if entry.get("provider") else None,
            base_url=str(entry["base_url"]) if entry.get("base_url") else None,
            api_key_env=str(entry["api_key_env"]) if entry.get("api_key_env") else None,
        )
    if "balanced" not in tiers:
        raise ConfigError(f"model profile {file} must define a 'balanced' tier")
    return ModelProfile(
        provider=str(data.get("provider", "unknown")),
        base_url=str(data.get("base_url", "")),
        tiers=tiers,
        thinking=str(data.get("thinking", "disabled")),
        raw=data,
    )


def call_cost_usd(
    *,
    tokens_in: int,
    tokens_out: int,
    pricing: PriceTable,
    cached_tokens: int = 0,
    peak: bool = False,
) -> float:
    cached = max(0, min(cached_tokens, tokens_in))
    missed = tokens_in - cached
    cost = (
        missed * pricing.input
        + cached * pricing.cache_hit_input
        + tokens_out * pricing.output
    ) / 1_000_000
    return round(cost * (2.0 if peak else 1.0), 8)


# --------------------------------------------------------------------- estimates

# Per-window and per-book token assumptions validated against plan section 3.6.
_WINDOW_SOURCE_WORDS = 700
_CACHE_HIT_FRACTION = 0.60

_PASS_TOKENS: dict[str, tuple[int, int]] = {
    "draft": (7000, 2000),
    "fidelity": (3800, 400),
    "literary": (3600, 400),
    "reviser": (5500, 1300),
}
_PASS_TIER: dict[str, Tier] = {
    "draft": "balanced",
    "fidelity": "balanced",
    "literary": "balanced",
    "reviser": "balanced",
}
_QUALITY_MODE_PASS_TIER: dict[str, Tier] = {
    "draft": "balanced",
    "fidelity": "quality",
    "literary": "quality",
    "reviser": "quality",
}


@dataclass(frozen=True)
class PassEstimate:
    name: str
    calls: int
    tokens_in: int
    tokens_out: int
    tier: str
    peak_usd: float
    offpeak_usd: float


@dataclass(frozen=True)
class ModeEstimate:
    mode: str
    words: int
    passes: list[PassEstimate]
    peak_usd: float
    offpeak_usd: float
    calls: int
    tokens_in: int
    tokens_out: int


def _pass_estimate(
    name: str, calls: int, tokens_in: int, tokens_out: int, pricing: PriceTable
) -> PassEstimate:
    cached = int(tokens_in * _CACHE_HIT_FRACTION)
    return PassEstimate(
        name=name,
        calls=calls,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tier="",
        peak_usd=call_cost_usd(
            tokens_in=tokens_in, tokens_out=tokens_out, pricing=pricing, cached_tokens=cached, peak=True
        ),
        offpeak_usd=call_cost_usd(
            tokens_in=tokens_in, tokens_out=tokens_out, pricing=pricing, cached_tokens=cached, peak=False
        ),
    )


def _scaled_book_pass(
    name: str, words: int, base_calls: int, base_in: int, base_out: int, pricing: PriceTable
) -> PassEstimate:
    factor = max(words / 90_000, 0.05)
    return _pass_estimate(
        name,
        max(1, round(base_calls * factor)),
        int(base_in * factor),
        int(base_out * factor),
        pricing,
    )


def estimate_book(words: int, mode: str, profile: ModelProfile) -> ModeEstimate:
    if mode not in ("economy", "standard", "quality"):
        raise ConfigError(f"unknown cost mode: {mode}")
    balanced = profile.tier("balanced")
    quality = profile.tier("quality") if "quality" in profile.tiers else balanced
    tier_map = _QUALITY_MODE_PASS_TIER if mode == "quality" else _PASS_TIER
    active = {
        "economy": ["draft", "fidelity"],
        "standard": ["draft", "fidelity", "literary", "reviser"],
        "quality": ["draft", "fidelity", "literary", "reviser"],
    }[mode]

    windows = max(1, math.ceil(words / _WINDOW_SOURCE_WORDS))
    passes: list[PassEstimate] = []
    for name in active:
        in_tokens, out_tokens = _PASS_TOKENS[name]
        tier_name = tier_map[name]
        tier = quality if tier_name == "quality" else balanced
        item = _pass_estimate(name, windows, windows * in_tokens, windows * out_tokens, tier.pricing)
        passes.append(
            PassEstimate(
                name=name,
                calls=item.calls,
                tokens_in=item.tokens_in,
                tokens_out=item.tokens_out,
                tier=tier_name,
                peak_usd=item.peak_usd,
                offpeak_usd=item.offpeak_usd,
            )
        )

    passes.append(
        _scaled_book_pass("analysis", words, 55, 200_000, 50_000, balanced.pricing)
    )
    passes.append(
        _scaled_book_pass("bookkeeping", words, 45, 120_000, 50_000, balanced.pricing)
    )

    return ModeEstimate(
        mode=mode,
        words=words,
        passes=passes,
        peak_usd=round(sum(item.peak_usd for item in passes), 4),
        offpeak_usd=round(sum(item.offpeak_usd for item in passes), 4),
        calls=sum(item.calls for item in passes),
        tokens_in=sum(item.tokens_in for item in passes),
        tokens_out=sum(item.tokens_out for item in passes),
    )


def estimate_all_modes(words: int, profile: ModelProfile) -> dict[str, ModeEstimate]:
    return {mode: estimate_book(words, mode, profile) for mode in ("economy", "standard", "quality")}


def select_cost_mode(
    words: int, profile: ModelProfile, cap_usd: float, *, peak: bool | None = None
) -> tuple[str, str]:
    """auto: standard when it fits the cap, otherwise economy. Never upgrades."""
    peak_now = is_peak() if peak is None else peak
    standard = estimate_book(words, "standard", profile)
    standard_cost = standard.peak_usd if peak_now else standard.offpeak_usd
    if standard_cost <= cap_usd:
        return "standard", f"auto: standard fits the ${cap_usd:g} cap (${standard_cost:.2f})"
    economy = estimate_book(words, "economy", profile)
    economy_cost = economy.peak_usd if peak_now else economy.offpeak_usd
    return "economy", (
        f"auto: standard would cost ${standard_cost:.2f} > ${cap_usd:g} cap; "
        f"using economy (${economy_cost:.2f})"
    )
