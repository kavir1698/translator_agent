"""Atomic file replacement helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        os.replace(temp_name, path)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    _replace(path, data)
    return path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> Path:
    _replace(path, text.encode(encoding))
    return path
