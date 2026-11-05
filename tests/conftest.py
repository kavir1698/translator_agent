from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    from tests.fixtures.build import build_all

    return build_all(tmp_path_factory.mktemp("books"))


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A TARJOMAN_HOME with the shipped configs and no books."""
    import shutil

    shutil.copytree(REPO_ROOT / "configs", tmp_path / "configs")
    (tmp_path / "books").mkdir()
    monkeypatch.setenv("TARJOMAN_HOME", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return tmp_path
