"""Deterministic 0..100 review risk score (plan 5.4)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any


def _value(row: Any) -> dict[str, Any]:
    raw = row["context_json"] if hasattr(row, "keys") else row.get("context", "{}")
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return dict(raw or {})


def compute_risk(
    segment: Any,
    issues: Sequence[Any],
    *,
    window_issue_count: int = 0,
    unapproved_suggestions: int = 0,
) -> int:
    score = 0
    codes = {str(issue["code"]) for issue in issues}
    if any(
        str(issue["severity"]) == "error" and str(issue["status"]) != "waived"
        for issue in issues
    ):
        score += 45
    if any(
        str(issue["severity"]) == "critical" and str(issue["status"]) != "resolved"
        for issue in issues
    ):
        score += 20
    if "L03" in codes:
        score += 15
    if "L08" in codes:
        l08 = [issue for issue in issues if str(issue["code"]) == "L08"]
        if any(str(issue["severity"]) == "error" for issue in l08):
            score += 15
        else:
            score += 10
    if window_issue_count > 5:
        score += 10
    if unapproved_suggestions >= 2:
        score += 8
    if str(segment["kind"]) == "verse":
        score += 7
    try:
        nodes = json.loads(segment["nodes_json"] or "[]")
    except (json.JSONDecodeError, KeyError, TypeError):
        nodes = []
    if any(isinstance(node, dict) and node.get("target_id") for node in nodes):
        score += 5
    return max(0, min(100, score))


def issue_context(row: Any) -> dict[str, Any]:
    return _value(row)
