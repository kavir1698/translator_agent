"""Project layout resolution."""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ConfigError


def project_root(start: Path | None = None) -> Path:
    """An explicit root wins; otherwise TARJOMAN_HOME, else the current directory."""
    if start is not None:
        return start.resolve()
    env_home = os.environ.get("TARJOMAN_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return Path.cwd().resolve()


def books_dir(root: Path | None = None, name: str | None = None) -> Path:
    return project_root(root) / (name or "books")


def book_dir(slug: str, root: Path | None = None, books_name: str | None = None) -> Path:
    return safe_join(books_dir(root, books_name), slug)


def safe_join(base: Path, *parts: str) -> Path:
    """Join under ``base`` and refuse to escape it."""
    target = base.joinpath(*parts).resolve()
    root = base.resolve()
    if not target.is_relative_to(root):
        raise ConfigError(f"path escapes the project workspace: {target}")
    return target


def resolve_config_path(value: str | None, root: Path | None = None) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_root(root) / path


def find_book_dir(
    root: Path | None = None, slug: str | None = None, books_name: str | None = None
) -> Path:
    """Find one book workspace, or the named one."""
    base = books_dir(root, books_name)
    if slug:
        candidate = safe_join(base, slug)
        if not candidate.is_dir():
            raise ConfigError(
                f"book workspace not found: {candidate}",
                hint=f"run `tj init <file> --slug {slug}` first",
            )
        return candidate
    candidates = sorted(
        path for path in base.glob("*") if path.is_dir() and (path / "book.db").exists()
    )
    if not candidates:
        raise ConfigError(
            f"no book workspace under {base}",
            hint="run `tj init <book.epub>` first, or pass --book <slug>",
        )
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates)
        raise ConfigError(
            f"multiple book workspaces found ({names})",
            hint="pass --book <slug> to choose one",
        )
    return candidates[0]
