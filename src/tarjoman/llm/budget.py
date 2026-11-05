"""Budget estimation and the pre-call guard (plan sections 9.5)."""

from __future__ import annotations

from dataclasses import dataclass

from tarjoman.core.cost import PriceTable, call_cost_usd
from tarjoman.core.errors import BudgetPaused


@dataclass
class BudgetGuard:
    cap_usd: float
    spent_usd: float = 0.0
    warn_at_fraction: float = 0.8
    allow_overrun: bool = False

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    @property
    def fraction_used(self) -> float:
        return self.spent_usd / self.cap_usd if self.cap_usd > 0 else 1.0

    def worst_case_usd(
        self, *, max_input_tokens: int, max_output_tokens: int, pricing: PriceTable
    ) -> float:
        return call_cost_usd(
            tokens_in=max_input_tokens, tokens_out=max_output_tokens, pricing=pricing, peak=True
        )

    def check(
        self,
        *,
        max_input_tokens: int,
        max_output_tokens: int,
        pricing: PriceTable,
        hint: str = "resume with the same command after raising budget.max_usd",
    ) -> None:
        if self.allow_overrun:
            return
        worst = self.worst_case_usd(
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            pricing=pricing,
        )
        if self.spent_usd + worst > self.cap_usd:
            raise BudgetPaused(
                f"budget cap reached: spent ${self.spent_usd:.2f} of ${self.cap_usd:.2f}; "
                f"next call could cost ${worst:.2f}",
                hint=hint,
            )

    def charge(self, cost_usd: float) -> None:
        self.spent_usd += max(0.0, cost_usd)

    def should_warn(self) -> bool:
        return self.fraction_used >= self.warn_at_fraction
