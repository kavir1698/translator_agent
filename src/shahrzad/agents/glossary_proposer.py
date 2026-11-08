"""A4 GlossaryProposer: fixed Persian renderings for auto mode (plan 6.1.6)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from shahrzad.core.cost import TierProfile
from shahrzad.core.db import Database
from shahrzad.core.models import BookBrief, Entity, GlossaryProposal, GlossaryProposals
from shahrzad.llm.cache import ResponseCache
from shahrzad.llm.promptlib import load as load_prompt
from shahrzad.llm.provider import Provider

from .analyst import brief_card
from .base import run_agent

AGENT = "A4"
PROMPT_ID = "p04_glossary"
BATCH_ENTITIES = 40


@dataclass(frozen=True)
class ProposalBatch:
    proposals: list[GlossaryProposal]
    refused: list[dict[str, str]]


def _existing_glossary(rows: Sequence[Any]) -> str:
    lines = [
        f"{row['term_en']} = {row['term_fa']} [{row['kind']}]"
        for row in rows
        if row["status"] == "approved" and row["term_fa"]
    ]
    return "\n".join(sorted(lines, key=str.casefold)) or "none"


def _entity_payload(entity: Entity) -> dict[str, Any]:
    payload = entity.model_dump()
    payload["kind"] = entity.kind.value if hasattr(entity.kind, "value") else entity.kind
    return payload


class GlossaryProposer:
    prompt_id = PROMPT_ID
    agent = AGENT

    def propose(
        self,
        *,
        provider: Provider,
        tier: TierProfile,
        db: Database,
        run_id: str | None,
        cache: ResponseCache | None,
        entities: Sequence[Entity],
        brief: BookBrief,
        existing_rows: Sequence[Any],
    ) -> ProposalBatch:
        prompt = load_prompt(self.prompt_id)
        existing = _existing_glossary(existing_rows)
        proposals: list[GlossaryProposal] = []
        refused: list[dict[str, str]] = []
        usable = [
            entity
            for entity in entities
            if entity.needs_translation and entity.en.strip() and entity.count > 0
        ]
        for start in range(0, len(usable), BATCH_ENTITIES):
            batch = usable[start : start + BATCH_ENTITIES]
            result = run_agent(
                provider=provider,
                prompt=prompt,
                schema_model=GlossaryProposals,
                variables={
                    "brief_card": brief_card(brief),
                    "existing_glossary": existing,
                    "entities_json": json.dumps(
                        [_entity_payload(entity) for entity in batch],
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                tier=tier,
                db=db,
                run_id=run_id,
                cache=cache,
            )
            proposals.extend(result.value.proposals)
            refused.extend(result.value.refuse)
        return ProposalBatch(proposals=proposals, refused=refused)
