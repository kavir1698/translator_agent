"""A11 ReadThroughEditor: source-free Persian reading of one chapter (plan 6.7, P11)."""

from __future__ import annotations

from typing import Any

from shahrzad.core.cost import TierProfile
from shahrzad.core.db import Database
from shahrzad.core.models import ReadThroughFindings
from shahrzad.llm.cache import ResponseCache
from shahrzad.llm.promptlib import load as load_prompt
from shahrzad.llm.provider import Provider

from .base import AgentResult, run_agent


class ReadThroughEditor:
    prompt_id = "p11_readthrough"

    def __init__(self) -> None:
        self.prompt = load_prompt(self.prompt_id)

    def read(
        self,
        *,
        provider: Provider,
        tier: TierProfile,
        db: Database,
        run_id: str | None,
        cache: ResponseCache | None,
        brief_card: str,
        style_state: str,
        chapter_fa_text: str,
    ) -> AgentResult[ReadThroughFindings]:
        variables: dict[str, Any] = {
            "brief_card": brief_card,
            "style_state": style_state,
            "chapter_fa_text": chapter_fa_text,
        }
        return run_agent(
            provider=provider,
            prompt=self.prompt,
            schema_model=ReadThroughFindings,
            variables=variables,
            tier=tier,
            db=db,
            run_id=run_id,
            cache=cache,
        )
