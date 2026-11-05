"""Prompt loading, front-matter, Jinja rendering and hashing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined

from tarjoman.core.errors import ConfigError
from tarjoman.core.ids import sha256_text

_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@dataclass(frozen=True)
class Prompt:
    id: str
    version: int
    agent: str
    tier: str
    temperature: float | None
    schema_name: str
    template: str
    path: Path

    def render(self, variables: dict[str, Any]) -> str:
        env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=False)
        try:
            return env.from_string(self.template).render(**variables)
        except Exception as exc:
            raise ConfigError(f"prompt {self.id} failed to render: {exc}") from exc

    def hash(self, rendered: str) -> str:
        """Rendered template hash + version; stable for a fixed prompt and inputs."""
        return sha256_text(f"{self.id}:{self.version}:{rendered}")


def load_prompt(path: Path) -> Prompt:
    text = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(text)
    if not match:
        raise ConfigError(f"prompt {path.name} has no YAML front-matter")
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"prompt {path.name} front-matter is invalid: {exc}") from exc
    template = text[match.end() :].strip()
    return Prompt(
        id=str(meta["id"]),
        version=int(meta["version"]),
        agent=str(meta["agent"]),
        tier=str(meta["tier"]),
        temperature=meta.get("temperature"),
        schema_name=str(meta["schema"]),
        template=template,
        path=path,
    )


def load_all(prompts_dir: Path | None = None) -> dict[str, Prompt]:
    directory = prompts_dir or PROMPTS_DIR
    prompts: dict[str, Prompt] = {}
    for path in sorted(directory.glob("*.md")):
        prompt = load_prompt(path)
        prompts[prompt.id] = prompt
    if not prompts:
        raise ConfigError(f"no prompts found in {directory}")
    return prompts


def load(prompt_id: str, prompts_dir: Path | None = None) -> Prompt:
    path = (prompts_dir or PROMPTS_DIR) / f"{prompt_id}.md"
    if not path.is_file():
        raise ConfigError(f"prompt not found: {path}")
    return load_prompt(path)
