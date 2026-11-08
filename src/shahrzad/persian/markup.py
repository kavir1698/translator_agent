"""Sentinel-aware markup and the persisted-text pipeline (SCHEMAS.md section 6)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from shahrzad.ingest.inline import _close_tag, _open_tag, escape_text

from .normalize import normalize_fa

_TOKEN = re.compile(r"⟨/?\d+⟩|⟦/?\d+⟧|\[\[#\d+\]\]|\[\[/\d+\]\]")
_STRAY = re.compile(r"[⟨⟩⟦⟧]")


@dataclass(frozen=True)
class MarkupProblem:
    code: str
    message: str


def _tokens(markup: str) -> list[str]:
    return _TOKEN.findall(markup)


def _token_id(token: str) -> int:
    match = re.search(r"\d+", token)
    if match is None:  # pragma: no cover - regex guarantees digits
        raise ValueError(f"not a sentinel: {token!r}")
    return int(match.group(0))


def _token_kind(token: str) -> Literal["open", "close", "plain"]:
    if "/" in token:
        return "close"
    if token.startswith(("[[#", "⟨", "⟦")):
        return "open"
    return "plain"


def expected_counts(nodes: list[dict[str, Any]]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for node in nodes:
        node_id = int(node["id"])
        if node["kind"] == "atomic":
            counts[node_id] = 1
        elif node.get("path"):
            counts[node_id] = 2
        else:
            counts[node_id] = 0
    return counts


def validate_markup(markup: str, nodes: list[dict[str, Any]]) -> list[MarkupProblem]:
    """Check ids, counts, atomicity and the single order rule (no crossing)."""
    problems: list[MarkupProblem] = []
    counts = expected_counts(nodes)
    seen: dict[int, int] = {}
    stack: list[int] = []
    tokens = _tokens(markup)
    for token in tokens:
        token_id = _token_id(token)
        kind = _token_kind(token)
        expected = counts.get(token_id)
        if expected is None:
            problems.append(MarkupProblem("unknown_sentinel", f"unknown sentinel id {token_id}"))
            continue
        seen[token_id] = seen.get(token_id, 0) + 1
        if expected == 1:
            if kind == "close":
                problems.append(MarkupProblem("atomic_closed", f"atomic {token_id} was closed"))
            continue
        if kind == "close":
            if not stack or stack[-1] != token_id:
                problems.append(
                    MarkupProblem("crossed_pairs", f"sentinel {token_id} closes out of order")
                )
                if token_id in stack:
                    while stack and stack[-1] != token_id:
                        stack.pop()
                    if stack:
                        stack.pop()
            else:
                stack.pop()
        else:
            stack.append(token_id)
    if stack:
        problems.append(MarkupProblem("unclosed_pair", f"unclosed sentinels: {stack}"))
    for node_id, expected in counts.items():
        if seen.get(node_id, 0) != expected:
            problems.append(
                MarkupProblem(
                    "wrong_count",
                    f"sentinel {node_id} appears {seen.get(node_id, 0)} time(s), expected {expected}",
                )
            )
    stray = _STRAY.search(_TOKEN.sub("", markup))
    if stray is not None:
        problems.append(MarkupProblem("stray_bracket", "stray sentinel bracket in text"))
    return problems


def is_valid(markup: str, nodes: list[dict[str, Any]]) -> bool:
    return not validate_markup(markup, nodes)


def normalize_markup(
    markup: str,
    *,
    digits: str = "persian",
    keep_diacritics: bool = False,
    ellipsis: str = "unicode",
    convert_number_separators: bool = False,
) -> str:
    """Normalize text runs only; sentinel tokens pass through byte-identically."""
    parts: list[str] = []
    position = 0
    for match in _TOKEN.finditer(markup):
        run = markup[position : match.start()]
        parts.append(
            normalize_fa(
                run,
                digits=digits,
                keep_diacritics=keep_diacritics,
                ellipsis=ellipsis,
                convert_number_separators=convert_number_separators,
                strip_edges=False,
            )
        )
        parts.append(match.group(0))
        position = match.end()
    parts.append(
        normalize_fa(
            markup[position:],
            digits=digits,
            keep_diacritics=keep_diacritics,
            ellipsis=ellipsis,
            convert_number_separators=convert_number_separators,
            strip_edges=False,
        )
    )
    return "".join(parts)


def strip_markup(markup: str, nodes: list[dict[str, Any]] | None = None) -> str:
    """Remove sentinels. Atomic <br> nodes become newlines so verse survives."""
    atomic = {
        int(node["id"]): str(node.get("atomic_html", "")) for node in (nodes or [])
    }
    parts: list[str] = []
    position = 0
    for match in _TOKEN.finditer(markup):
        parts.append(markup[position : match.start()])
        token_id = _token_id(match.group(0))
        if _token_kind(match.group(0)) != "close" and atomic.get(token_id, "").startswith("<br"):
            parts.append("\n")
        position = match.end()
    parts.append(markup[position:])
    return "".join(parts)


@dataclass(frozen=True)
class NormalizedTranslation:
    fa_markup: str
    fa_text: str
    problems: list[MarkupProblem]


def normalize_translation(
    markup: str,
    nodes: list[dict[str, Any]],
    *,
    digits: str = "persian",
    keep_diacritics: bool = False,
    ellipsis: str = "unicode",
) -> NormalizedTranslation:
    """parse -> validate ids -> normalize text runs -> reassemble -> strip -> L02."""
    problems = validate_markup(markup, nodes)
    normalized = normalize_markup(
        markup, digits=digits, keep_diacritics=keep_diacritics, ellipsis=ellipsis
    )
    return NormalizedTranslation(
        fa_markup=normalized,
        fa_text=strip_markup(normalized, nodes),
        problems=problems,
    )


def rebuilt_html(markup: str, nodes: list[dict[str, Any]]) -> str:
    """Rebuild translated HTML by walking the sentinel sequence in model order."""
    by_id = {int(node["id"]): node for node in nodes}
    out: list[str] = []
    stack: list[str] = []
    position = 0

    def close_to(length: int) -> None:
        while len(stack) > length:
            out.append(_close_tag(stack.pop()))

    def open_path(path: list[str]) -> None:
        common = 0
        for left, right in zip(stack, path, strict=False):
            if left != right:
                break
            common += 1
        close_to(common)
        for step in path[common:]:
            out.append(_open_tag(step))
            stack.append(step)

    for match in _TOKEN.finditer(markup):
        plain = markup[position : match.start()]
        if plain:
            out.append(escape_text(plain))
        token = match.group(0)
        node = by_id.get(_token_id(token))
        position = match.end()
        if node is None:
            continue
        path = [str(step) for step in node.get("path", [])]
        if _token_kind(token) == "close":
            open_path(path[:-1])
        elif node["kind"] == "atomic":
            open_path(path)
            out.append(str(node.get("atomic_html", "")))
            open_path(path[:-1])
        else:
            open_path(path)
    tail = markup[position:]
    if tail:
        out.append(escape_text(tail))
    close_to(0)
    return "".join(out)
