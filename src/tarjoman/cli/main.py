"""CLI entry point: global options, error-to-exit-code mapping, command registry."""

from __future__ import annotations

import sys

import typer

from tarjoman.core.logging import configure_logging

from . import (
    analyze_cmd,
    doctor_cmd,
    estimate_cmd,
    export_cmd,
    init_cmd,
    inspect_cmd,
    retranslate_cmd,
    translate_cmd,
)
from .anchors_cmd import anchors_app
from .glossary_cmd import glossary_app
from .qa_cmd import qa_app
from .review_cmd import review_app

app = typer.Typer(
    name="tj",
    help="Tarjoman — agentic English to Persian literary translator.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)


@app.callback()
def main(
    ctx: typer.Context,
    book: str | None = typer.Option(
        None, "--book", "-b", help="Book slug (default: the only workspace)."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging."),
) -> None:
    """A resumable, cost-aware pipeline for one personal library."""
    ctx.obj = {"book": book}
    configure_logging("DEBUG" if verbose else "INFO", json_logs=True)


app.command("doctor")(doctor_cmd.doctor)
app.command("estimate")(estimate_cmd.estimate)
app.command("init")(init_cmd.init)
app.command("inspect")(inspect_cmd.inspect)
app.command("analyze")(analyze_cmd.analyze)
app.command("translate")(translate_cmd.translate)
app.command("retranslate")(retranslate_cmd.retranslate)
app.command("export")(export_cmd.export)
app.add_typer(glossary_app, name="glossary")
app.add_typer(qa_app, name="qa")
app.add_typer(anchors_app, name="anchors")
app.add_typer(review_app, name="review")


if __name__ == "__main__":
    sys.exit(app())
