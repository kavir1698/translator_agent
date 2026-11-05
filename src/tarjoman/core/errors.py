"""Typed exceptions and the stable CLI exit-code contract."""

from __future__ import annotations

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_PAUSED = 3
EXIT_INCOMPLETE = 4


class TarjomanError(Exception):
    """Base class for every error the CLI knows how to report."""

    exit_code = EXIT_RUNTIME

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint

    def __str__(self) -> str:
        if self.hint:
            return f"{self.message}\nNext: {self.hint}"
        return self.message


class UsageError(TarjomanError):
    exit_code = EXIT_USAGE


class ConfigError(UsageError):
    pass


class RuntimeFailure(TarjomanError):
    exit_code = EXIT_RUNTIME


class BudgetPaused(TarjomanError):
    exit_code = EXIT_PAUSED


class IncompleteError(RuntimeFailure):
    """Finished with unresolved error issues or incomplete coverage (exit 4)."""

    exit_code = EXIT_INCOMPLETE


class InvalidTransition(RuntimeFailure):
    pass


class ProviderError(RuntimeFailure):
    """A provider call failed permanently."""


class TransientProviderError(ProviderError):
    """A provider call failed in a way that is worth retrying."""


class MissingFixture(ProviderError):
    pass


class SchemaValidationError(RuntimeFailure):
    pass


class PdfNoTextLayer(RuntimeFailure):
    def __init__(self) -> None:
        super().__init__(
            "PDF_NO_TEXT_LAYER: the PDF has no usable text layer; OCR is not supported in v1",
            hint="supply an EPUB, or a PDF exported with selectable text",
        )
