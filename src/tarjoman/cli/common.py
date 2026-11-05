"""Shared CLI helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tarjoman.core.config import Settings, load_settings, model_profile
from tarjoman.core.cost import ModelProfile
from tarjoman.core.paths import books_dir, project_root
from tarjoman.llm.litellm_provider import LiteLLMProvider
from tarjoman.llm.mock_provider import MockProvider
from tarjoman.llm.provider import Provider


@dataclass(frozen=True)
class Context:
    root: Path
    settings: Settings
    profile: ModelProfile
    slug: str | None


def detect_slug(root: Path, explicit: str | None, books_name: str | None = None) -> str | None:
    if explicit:
        return explicit
    base = books_dir(root, books_name)
    if not base.is_dir():
        return None
    candidates = sorted(
        path.name for path in base.glob("*") if path.is_dir() and (path / "book.db").exists()
    )
    return candidates[0] if len(candidates) == 1 else None


def load_context(slug: str | None = None) -> Context:
    root = project_root()
    base_settings = load_settings(root=root)
    books_name = base_settings.project.books_dir
    resolved = detect_slug(root, slug, books_name)
    settings = (
        load_settings(root=root, slug=resolved, books_dir_name=books_name)
        if resolved
        else base_settings
    )
    profile = model_profile(settings, root)
    return Context(root=root, settings=settings, profile=profile, slug=resolved)


def parse_int_list(value: str | None) -> list[int] | None:
    if not value:
        return None
    result: list[int] = []
    for part in value.split(","):
        item = part.strip()
        if not item:
            continue
        if "-" in item:
            start, end = item.split("-", 1)
            result.extend(range(int(start), int(end) + 1))
        else:
            result.append(int(item))
    return sorted(set(result)) or None


def parse_str_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()] or None


def build_provider(context: Context) -> Provider:
    """Mock fixtures when requested (offline tests), otherwise the real provider."""
    fixtures = os.environ.get("TARJOMAN_MOCK_FIXTURES")
    if fixtures:
        return MockProvider(fixtures_dir=fixtures)
    return LiteLLMProvider(
        profile=context.profile,
        timeout_s=context.settings.provider.timeout_s,
        max_retries=context.settings.provider.max_retries,
        thinking=context.settings.provider.thinking,
    )
