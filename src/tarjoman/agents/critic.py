"""A6 FidelityCritic and A7 LiteraryEditor (plan 6.5, P06/P07)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tarjoman.core.cost import TierProfile
from tarjoman.core.db import Database
from tarjoman.core.models import Critique
from tarjoman.llm.cache import ResponseCache
from tarjoman.llm.promptlib import load as load_prompt
from tarjoman.llm.provider import Provider

from .base import AgentResult, run_agent


class Critic:
    def __init__(self, prompt_id: str) -> None:
        self.prompt_id = prompt_id
        self.prompt = load_prompt(prompt_id)

    def review(
        self,
        *,
        provider: Provider,
        tier: TierProfile,
        db: Database,
        run_id: str | None,
        cache: ResponseCache | None,
        variables: dict[str, Any],
        segments: Sequence[Any],
        draft: str,
    ) -> AgentResult[Critique]:
        payload = {
            **variables,
            "segments": variables.get("segments", ""),
            "draft": draft,
        }
        return run_agent(
            provider=provider,
            prompt=self.prompt,
            schema_model=Critique,
            variables=payload,
            tier=tier,
            db=db,
            run_id=run_id,
            cache=cache,
        )


class FidelityCritic(Critic):
    def __init__(self) -> None:
        super().__init__("p06_fidelity_critic")


class LiteraryEditor(Critic):
    def __init__(self) -> None:
        super().__init__("p07_literary_editor")
