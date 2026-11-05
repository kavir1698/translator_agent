from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tarjoman.core.cost import (
    PriceTable,
    call_cost_usd,
    estimate_all_modes,
    estimate_book,
    is_peak,
    load_model_profile,
    select_cost_mode,
)


@pytest.fixture
def profile(repo_root):
    return load_model_profile(repo_root / "configs" / "models" / "deepseek.yaml")


def test_peak_windows():
    assert is_peak(datetime(2026, 11, 5, 2, 0, tzinfo=UTC))  # Thursday 02:00
    assert is_peak(datetime(2026, 11, 5, 7, 30, tzinfo=UTC))
    assert not is_peak(datetime(2026, 11, 5, 5, 0, tzinfo=UTC))
    assert not is_peak(datetime(2026, 11, 7, 2, 0, tzinfo=UTC))  # Saturday


def test_call_cost_cache_discount():
    prices = PriceTable(cache_hit_input=0.003, input=0.30, output=1.20)
    full = call_cost_usd(tokens_in=1_000_000, tokens_out=0, pricing=prices)
    cached = call_cost_usd(tokens_in=1_000_000, tokens_out=0, pricing=prices, cached_tokens=600_000)
    assert full == pytest.approx(0.30)
    assert cached == pytest.approx(0.4 * 0.30 + 0.6 * 0.003)
    assert call_cost_usd(tokens_in=0, tokens_out=1_000_000, pricing=prices, peak=True) == pytest.approx(2.40)


def test_estimate_ordering(profile):
    modes = estimate_all_modes(90_000, profile)
    assert modes["economy"].peak_usd < modes["standard"].peak_usd < modes["quality"].peak_usd
    assert modes["standard"].peak_usd == pytest.approx(modes["standard"].offpeak_usd * 2, rel=0.01)
    assert 0.5 <= modes["standard"].peak_usd <= 6.0


def test_estimate_scales_with_words(profile):
    small = estimate_book(10_000, "standard", profile)
    large = estimate_book(90_000, "standard", profile)
    assert large.calls > small.calls
    assert large.peak_usd > small.peak_usd


def test_auto_mode_downgrades(profile):
    mode, reason = select_cost_mode(90_000, profile, cap_usd=10, peak=True)
    assert mode == "standard"
    assert "fits" in reason

    mode, reason = select_cost_mode(900_000, profile, cap_usd=1, peak=True)
    assert mode == "economy"
    assert "economy" in reason
