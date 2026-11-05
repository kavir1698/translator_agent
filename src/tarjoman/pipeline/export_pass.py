"""`tj export` orchestration: load once, render the requested formats."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from tarjoman.core.config import Settings
from tarjoman.core.errors import ConfigError
from tarjoman.core.paths import find_book_dir, safe_join
from tarjoman.export.assets import load_source_assets
from tarjoman.export.bilingual_html import write_bilingual_html
from tarjoman.export.docx_writer import write_docx
from tarjoman.export.epub_writer import write_epub
from tarjoman.export.loader import load_export_book
from tarjoman.export.markdown_writer import write_markdown
from tarjoman.export.pdf_convert import convert_docx_to_pdf
from tarjoman.export.plain_text import write_plain_text

_ALLOWED = {"epub", "docx", "md", "txt"}


@dataclass
class ExportOptions:
    formats: list[str] | None = None
    out: Path | None = None
    pdf: bool = False
    bilingual: bool = False
    allow_incomplete: bool = False
    generated_at: str | None = None


@dataclass
class ExportResult:
    paths: dict[str, Path]
    warnings: list[str] = field(default_factory=list)
    incomplete: bool = False


def _formats(options: ExportOptions, settings: Settings) -> list[str]:
    requested = list(options.formats) if options.formats is not None else list(settings.export.formats)
    result: list[str] = []
    for raw in requested:
        name = raw.strip().lower()
        if not name:
            continue
        if name not in _ALLOWED:
            allowed = ", ".join(sorted(_ALLOWED))
            raise ConfigError(
                f"unknown export format: {raw!r}",
                hint=f"choose from: {allowed}",
            )
        if name not in result:
            result.append(name)
    if not result:
        raise ConfigError(
            "no export formats selected",
            hint="pass --formats epub,docx,md,txt",
        )
    return result


def _output_dir(base: Path, value: Path | None) -> Path:
    if value is None:
        return base / "out"
    if value.is_absolute():
        return value
    return safe_join(base, str(value))


def run_export(
    *,
    root: Path,
    slug: str | None,
    settings: Settings,
    options: ExportOptions | None = None,
) -> ExportResult:
    """Export without writing to the database. The EPUB gate runs in the loader."""
    options = options or ExportOptions()
    warnings: list[str] = []
    formats = _formats(options, settings)
    write_formats = list(formats)
    if options.pdf and "docx" not in write_formats:
        write_formats.append("docx")

    base = find_book_dir(root, slug, settings.project.books_dir)
    book = load_export_book(
        root=root,
        slug=slug,
        settings=settings,
        allow_incomplete=options.allow_incomplete,
        generated_at=options.generated_at,
    )
    out = _output_dir(base, options.out)
    out.mkdir(parents=True, exist_ok=True)
    assets = load_source_assets(book)
    if not book.title_fa and any(name in {"epub", "docx"} for name in write_formats):
        warnings.append(
            "book.title_fa is not set; dc:title and the DOCX title use the source title. "
            "Set book.title_fa in config.yaml for an all-Persian export."
        )

    paths: dict[str, Path] = {}
    for name in write_formats:
        target = out / f"{book.slug}.fa.{name}"
        if name == "epub":
            paths[name] = write_epub(book, target, settings, assets)
        elif name == "docx":
            paths[name] = write_docx(book, target, settings, assets)
        elif name == "md":
            paths[name] = write_markdown(book, target)
        elif name == "txt":
            paths[name] = write_plain_text(book, target)
    if options.pdf:
        pdf = convert_docx_to_pdf(paths["docx"], out)
        warnings.append(pdf.message)
        if pdf.path is not None:
            paths["pdf"] = pdf.path
    if options.bilingual:
        paths["bilingual"] = write_bilingual_html(
            book, out / f"{book.slug}.bilingual.html", settings
        )
    if book.incomplete:
        if "epub" in paths:
            warnings.append(
                "translation is incomplete; the EPUB includes a TRANSLATION INCOMPLETE page"
            )
        else:
            warnings.append("translation is incomplete; the output is marked experimental")
    return ExportResult(paths=paths, warnings=warnings, incomplete=book.incomplete)
