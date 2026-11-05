"""Console handles and the CLI error-to-exit-code decorator."""

from __future__ import annotations

import functools
import sys
from collections.abc import Callable
from typing import Any, TypeVar

import typer
from rich.console import Console

from tarjoman.core.errors import TarjomanError


def _force_utf8() -> None:
    """Persian terms must not crash a cp1252 Windows console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            continue


_force_utf8()

console = Console(legacy_windows=False)
err_console = Console(stderr=True, legacy_windows=False)
F = TypeVar("F", bound=Callable[..., Any])


def handle_errors(func: F) -> F:
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except TarjomanError as exc:
            err_console.print(f"[red]{exc.message}[/red]")
            if exc.hint:
                err_console.print(f"[dim]Next: {exc.hint}[/dim]")
            raise typer.Exit(exc.exit_code) from exc

    return wrapper  # type: ignore[return-value]
