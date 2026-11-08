from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from shahrzad.core.config import load_model_profile, load_settings
from shahrzad.core.errors import ConfigError


def test_defaults_load(repo_root: Path):
    settings = load_settings(root=repo_root)
    assert settings.provider.thinking == "disabled"
    assert settings.budget.max_usd == 10
    assert settings.glossary.mode == "off"
    assert settings.pipeline.window.max_paragraphs == 8
    assert settings.export.epub.rtl is True


def test_env_overrides_yaml(repo_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHAHRZAD_BUDGET__MAX_USD", "3.5")
    settings = load_settings(root=repo_root)
    assert settings.budget.max_usd == 3.5


def test_cli_override_wins(repo_root: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SHAHRZAD_BUDGET__MAX_USD", "3.5")
    settings = load_settings(root=repo_root, overrides={"budget": {"max_usd": 7}})
    assert settings.budget.max_usd == 7


def test_unknown_key_rejected(repo_root: Path, tmp_path: Path):
    root = tmp_path / "proj"
    shutil.copytree(repo_root / "configs", root / "configs")
    defaults = yaml.safe_load((root / "configs" / "default.yaml").read_text(encoding="utf-8"))
    defaults["budget"]["max_usd_typo"] = 1
    (root / "configs" / "default.yaml").write_text(yaml.safe_dump(defaults), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(root=root)


def test_provided_glossary_requires_path(repo_root: Path, tmp_path: Path):
    root = tmp_path / "proj"
    shutil.copytree(repo_root / "configs", root / "configs")
    overrides = {"glossary": {"mode": "provided", "path": None}}
    with pytest.raises(ConfigError):
        load_settings(root=root, overrides=overrides)


def test_model_profile(repo_root: Path):
    profile = load_model_profile(repo_root / "configs" / "models" / "deepseek.yaml")
    assert profile.tiers["balanced"].model == "deepseek-flash"
    assert profile.tiers["quality"].model == "gpt-6-luna"
    assert profile.provider_for(profile.tiers["quality"]) == "openrouter"
    assert profile.tiers["quality"].api_key_env == "OPENROUTER_API_KEY"
    assert profile.thinking == "disabled"
    assert profile.tiers["balanced"].pricing.input == 0.15
