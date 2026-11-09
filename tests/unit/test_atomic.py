from __future__ import annotations

import pytest

from shahrzad.core import atomic


def test_atomic_write_retries_transient_replace_lock(monkeypatch, tmp_path):
    calls = {"n": 0}
    real_replace = atomic.os.replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(5, "Access is denied", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(atomic.os, "replace", flaky_replace)
    target = tmp_path / "ledger.jsonl"

    atomic.atomic_write_text(target, "hello\n")

    assert target.read_text(encoding="utf-8") == "hello\n"
    assert calls["n"] == 3
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]


def test_atomic_write_raises_after_retries_and_cleans_temp(monkeypatch, tmp_path):
    calls = {"n": 0}

    def always_locked(src, dst):
        calls["n"] += 1
        raise PermissionError(5, "Access is denied", str(dst))

    monkeypatch.setattr(atomic.os, "replace", always_locked)
    target = tmp_path / "ledger.jsonl"

    with pytest.raises(PermissionError):
        atomic.atomic_write_text(target, "hello\n")

    assert calls["n"] == atomic._REPLACE_ATTEMPTS
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
