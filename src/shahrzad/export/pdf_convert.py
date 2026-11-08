"""DOCX to PDF conversion through LibreOffice, with a graceful fallback."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_CAVEAT = (
    "a LibreOffice RTL PDF is readable but not print-ready "
    "(no kashida justification, limited page mirroring, endnote styling varies)"
)
_TIMEOUT_S = 300


@dataclass
class PdfResult:
    path: Path | None
    available: bool
    message: str


def _command(docx_path: Path, out_dir: Path) -> str:
    return (
        "soffice --headless --convert-to pdf "
        f'--outdir "{out_dir}" "{docx_path}"'
    )


def _failure(command: str, detail: str) -> PdfResult:
    return PdfResult(
        path=None,
        available=True,
        message=f"PDF conversion failed: {detail}. Manual command: {command}; {_CAVEAT}.",
    )


def convert_docx_to_pdf(docx_path: Path, out_dir: Path) -> PdfResult:
    """Convert one DOCX; a missing LibreOffice is reported, never raised."""
    command = _command(docx_path, out_dir)
    executable = shutil.which("soffice")
    if executable is None:
        return PdfResult(
            path=None,
            available=False,
            message=(
                "PDF conversion skipped: LibreOffice (soffice) was not found. "
                f"Run manually: {command}; {_CAVEAT}."
            ),
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=".pdf-", dir=out_dir))
    try:
        completed = subprocess.run(
            [
                executable,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(temp_dir),
                str(docx_path),
            ],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            return _failure(
                command,
                f"soffice exited with {completed.returncode}"
                + (f": {detail}" if detail else ""),
            )
        produced = temp_dir / f"{docx_path.stem}.pdf"
        if not produced.is_file():
            return _failure(command, f"soffice did not create {produced.name}")
        try:
            data = produced.read_bytes()
        except OSError as exc:
            return _failure(command, str(exc))
        if not data:
            return _failure(command, f"{produced.name} is empty")
        if not data.startswith(b"%PDF"):
            return _failure(command, f"{produced.name} does not begin with %PDF")
        final = out_dir / f"{docx_path.stem}.pdf"
        os.replace(produced, final)
        return PdfResult(
            path=final,
            available=True,
            message=f"PDF written with LibreOffice: {final}; {_CAVEAT}.",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _failure(command, str(exc))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
