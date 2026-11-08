"""A9 LedgerCurator: chapter-close continuity memory (plan 6.6, P09)."""

from __future__ import annotations

from typing import Any

from shahrzad.core.cost import TierProfile
from shahrzad.core.db import Database
from shahrzad.core.models import LedgerUpdate
from shahrzad.llm.cache import ResponseCache
from shahrzad.llm.promptlib import load as load_prompt
from shahrzad.llm.provider import Provider

from .base import AgentResult, run_agent


class LedgerCurator:
    prompt_id = "p09_ledger_curator"

    def __init__(self) -> None:
        self.prompt = load_prompt(self.prompt_id)

    def curate(
        self,
        *,
        provider: Provider,
        tier: TierProfile,
        db: Database,
        run_id: str | None,
        cache: ResponseCache | None,
        chapter_index: int,
        chapter_summary: str,
        chapter_excerpt_fa: str,
        new_terms: str,
        translator_notes: str,
        existing_ledger: str,
    ) -> AgentResult[LedgerUpdate]:
        variables: dict[str, Any] = {
            "chapter_index": chapter_index,
            "chapter_summary": chapter_summary,
            "chapter_excerpt_fa": chapter_excerpt_fa,
            "new_terms": new_terms,
            "translator_notes": translator_notes,
            "existing_ledger": existing_ledger,
        }
        return run_agent(
            provider=provider,
            prompt=self.prompt,
            schema_model=LedgerUpdate,
            variables=variables,
            tier=tier,
            db=db,
            run_id=run_id,
            cache=cache,
        )
