"""Console handles and the CLI error-to-exit-code decorator."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any, TypeVar

import typer
from rich.console import Console

from tarjoman.core.errors import TarjomanError

console = Console()
err_console = Console(stderr=True)
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
