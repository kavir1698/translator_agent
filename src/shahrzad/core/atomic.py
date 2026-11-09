"""Atomic file replacement helpers."""

from __future__ import annotations

import errno
import os
import tempfile
import time
from pathlib import Path

_REPLACE_ATTEMPTS = 5
_REPLACE_DELAY_S = 0.05


def _transient_replace_error(exc: OSError) -> bool:
    """True for Windows sharing/permission locks that usually clear on retry."""
    if getattr(exc, "winerror", None) in {5, 32, 33}:
        return True
    return isinstance(exc, PermissionError) or exc.errno in {
        errno.EACCES,
        errno.EBUSY,
        errno.EPERM,
    }


def _replace(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temp_name, path)
                return
            except OSError as exc:
                if attempt + 1 >= _REPLACE_ATTEMPTS or not _transient_replace_error(exc):
                    raise
                time.sleep(_REPLACE_DELAY_S * (2**attempt))
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, data: bytes) -> Path:
    _replace(path, data)
    return path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> Path:
    _replace(path, text.encode(encoding))
    return path
