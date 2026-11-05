"""A10 ConsistencyAuditor: cross-chapter contradiction detection (plan 6.7, P10)."""

from __future__ import annotations

from typing import Any

from tarjoman.core.cost import TierProfile
from tarjoman.core.db import Database
from tarjoman.core.models import AuditFindings
from tarjoman.llm.cache import ResponseCache
from tarjoman.llm.promptlib import load as load_prompt
from tarjoman.llm.provider import Provider

from .base import AgentResult, run_agent


class ConsistencyAuditor:
    prompt_id = "p10_consistency_auditor"

    def __init__(self) -> None:
        self.prompt = load_prompt(self.prompt_id)

    def audit(
        self,
        *,
        provider: Provider,
        tier: TierProfile,
        db: Database,
        run_id: str | None,
        cache: ResponseCache | None,
        chapter_index: int,
        chapter_summary: str,
        ledger: str,
        glossary: str,
        sample_pairs: str,
    ) -> AgentResult[AuditFindings]:
        variables: dict[str, Any] = {
            "chapter_index": chapter_index,
            "chapter_summary": chapter_summary,
            "ledger": ledger,
            "glossary": glossary,
            "sample_pairs": sample_pairs,
        }
        return run_agent(
            provider=provider,
            prompt=self.prompt,
            schema_model=AuditFindings,
            variables=variables,
            tier=tier,
            db=db,
            run_id=run_id,
            cache=cache,
        )
