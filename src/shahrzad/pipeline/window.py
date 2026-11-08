"""Deterministic window planner (plan 6.3). No I/O, no model calls."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from shahrzad.core.ids import sha256_text, window_id


@dataclass(frozen=True)
class Window:
    id: str
    chapter_id: int
    chapter_idx: int
    index: int
    segments: list[Any] = field(default_factory=list)
    overlap: list[Any] = field(default_factory=list)

    @property
    def segment_ids(self) -> list[str]:
        return [str(segment["id"]) for segment in self.segments]

    @property
    def tokens(self) -> int:
        return sum(max(1, int(segment["tokens_est"])) for segment in self.segments)


def _is_dialogue(segment: Any) -> bool:
    return float(segment["dialogue_ratio"] or 0.0) > 0.15


def _heading_only(segments: Sequence[Any]) -> bool:
    return bool(segments) and all(str(segment["kind"]) == "heading" for segment in segments)


def plan_windows(
    segments: Sequence[Any],
    *,
    chapter_id: int,
    chapter_idx: int,
    max_paragraphs: int = 8,
    max_source_tokens: int = 900,
    overlap_paragraphs: int = 2,
) -> list[Window]:
    """Group translatable segments into ordered windows, never splitting a segment."""
    usable = [segment for segment in segments if str(segment["status"]) != "skipped"]
    windows: list[Window] = []
    current: list[Any] = []
    current_tokens = 0
    previous: list[Any] = []

    def flush() -> None:
        nonlocal current, current_tokens, previous
        if not current:
            return
        index = len(windows) + 1
        overlap = previous[-overlap_paragraphs:] if overlap_paragraphs > 0 else []
        windows.append(
            Window(
                id=window_id(chapter_idx, index),
                chapter_id=chapter_id,
                chapter_idx=chapter_idx,
                index=index,
                segments=list(current),
                overlap=list(overlap),
            )
        )
        previous = list(current)
        current = []
        current_tokens = 0

    for position, segment in enumerate(usable):
        tokens = max(1, int(segment["tokens_est"]))
        force_heading_pair = bool(current) and _heading_only(current) and len(current) == 1
        fits = force_heading_pair or (
            len(current) < max_paragraphs
            and (current_tokens + tokens <= max_source_tokens or not current)
        )
        if not fits:
            flush()
        current.append(segment)
        current_tokens += tokens

        nxt = usable[position + 1] if position + 1 < len(usable) else None
        if nxt is None:
            continue
        # A chapter's first heading must travel with at least one following block.
        if _heading_only(current) and len(current) == 1:
            continue
        next_tokens = max(1, int(nxt["tokens_est"]))
        dialogue_pair = _is_dialogue(segment) and _is_dialogue(nxt)
        at_paragraph_limit = len(current) >= max_paragraphs
        if at_paragraph_limit and dialogue_pair and current_tokens + next_tokens <= max_source_tokens:
            continue
        if at_paragraph_limit or current_tokens + next_tokens > max_source_tokens:
            flush()
    flush()
    return windows


def plan_hash(windows: Sequence[Window]) -> str:
    payload = [
        [window.chapter_id, window.id, [str(segment["id"]) for segment in window.segments]]
        for window in windows
    ]
    return f"sha256:{sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))}"


def todo_segments(window: Window, *, force: bool = False) -> list[Any]:
    """Segments in the window that still need a translation."""
    if force:
        return list(window.segments)
    return [segment for segment in window.segments if str(segment["status"]) != "done"]
