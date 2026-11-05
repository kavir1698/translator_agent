"""A8 Reviser: applies quoted critiques and deterministic lint repairs (P08)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from tarjoman.core.cost import TierProfile
from tarjoman.core.db import Database
from tarjoman.core.models import WindowTranslation
from tarjoman.llm.cache import ResponseCache
from tarjoman.llm.promptlib import load as load_prompt
from tarjoman.llm.provider import Provider

from .base import AgentResult, run_agent


class Reviser:
    prompt_id = "p08_reviser"

    def __init__(self) -> None:
        self.prompt = load_prompt(self.prompt_id)

    def revise(
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
        issues: Sequence[dict[str, Any]] = (),
        repair_only: bool = False,
        lint_errors: str = "",
    ) -> AgentResult[WindowTranslation]:
        numbered = [
            {"issue_index": index, **issue} for index, issue in enumerate(issues)
        ]
        payload = {
            **variables,
            "draft": draft,
            "issues": _format_issues(numbered),
            "repair_only": repair_only,
            "lint_errors": lint_errors,
            "segments": variables.get("segments", ""),
        }
        return run_agent(
            provider=provider,
            prompt=self.prompt,
            schema_model=WindowTranslation,
            variables=payload,
            tier=tier,
            db=db,
            run_id=run_id,
            cache=cache,
        )


def _format_issues(issues: Sequence[dict[str, Any]]) -> str:
    if not issues:
        return "(none)"
    lines: list[str] = []
    for issue in issues:
        lines.append(
            f"{issue.get('issue_index')}. [{issue.get('segment_id')}] "
            f"({issue.get('type')}/{issue.get('severity')}) {issue.get('problem')}"
        )
        if issue.get("quote_source"):
            lines.append(f"   source: {issue['quote_source']}")
        if issue.get("quote_target"):
            lines.append(f"   target: {issue['quote_target']}")
        if issue.get("suggested_fix"):
            lines.append(f"   fix: {issue['suggested_fix']}")
    return "\n".join(lines)
