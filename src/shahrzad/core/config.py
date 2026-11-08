"""Configuration: defaults -> book config -> environment -> CLI overrides."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .cost import ModelProfile, load_model_profile
from .errors import ConfigError
from .paths import book_dir, project_root, resolve_config_path


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectConfig(_Section):
    name: str = "shahrzad"
    books_dir: str = "books"
    timezone: str = "UTC"


class BookConfig(_Section):
    slug: str | None = None
    source_lang: str = "en"
    target_lang: str = "fa"
    rights_basis: Literal["public_domain", "licensed", "user_supplied"] = "user_supplied"
    translator: str = ""
    title_fa: str | None = None
    cover: str | None = None


class ProviderConfig(_Section):
    default: str = "deepseek"
    profile: str = "deepseek"
    models: str = "configs/models/deepseek.yaml"
    thinking: Literal["disabled", "low", "medium", "high"] = "disabled"
    thinking_overrides: dict[str, str] = Field(default_factory=dict)
    reasoning_allowance: int = 2048
    tariff: Literal["auto", "peak", "offpeak"] = "auto"
    timeout_s: int = 180
    max_retries: int = 4
    max_inflight: int = 4
    fallbacks: list[str] = Field(default_factory=list)
    log_prompts: bool = False


class GlossaryConfig(_Section):
    mode: Literal["off", "provided", "auto"] = "off"
    path: str | None = None
    approval: Literal["auto", "manual"] = "auto"
    auto_approve_min_confidence: float = 0.90

    @model_validator(mode="after")
    def _provided_needs_path(self) -> GlossaryConfig:
        if self.mode == "provided" and not self.path:
            raise ValueError("glossary.mode=provided requires glossary.path")
        return self


class WindowConfig(_Section):
    max_paragraphs: int = 8
    max_source_tokens: int = 900
    min_paragraphs: int = 4
    overlap_paragraphs: int = 2


class CriticsConfig(_Section):
    fidelity: bool = True
    style: bool = True
    max_revision_rounds: int = 1
    force_fidelity_on_length_outlier: bool = True


class PassesConfig(_Section):
    readthrough: bool = True
    backtranslate: bool = False
    consistency_audit: bool = True


class AnalyzeConfig(_Section):
    sample_token_budget: int = 18000
    summary_model_tier: Literal["quality", "balanced", "cheap"] = "cheap"
    brief_approval: Literal["auto", "manual"] = "auto"


class PipelineConfig(_Section):
    cost_mode: Literal["auto", "economy", "standard", "quality"] = "auto"
    window: WindowConfig = Field(default_factory=WindowConfig)
    critics: CriticsConfig = Field(default_factory=CriticsConfig)
    passes: PassesConfig = Field(default_factory=PassesConfig)
    analyze: AnalyzeConfig = Field(default_factory=AnalyzeConfig)
    resume: bool = True


class PersianConfig(_Section):
    digits: Literal["persian", "ascii"] = "persian"
    keep_diacritics: bool = False
    zwnj_repair: Literal["off", "high_precision"] = "high_precision"
    ellipsis: Literal["unicode", "ascii"] = "unicode"
    auto_fix: list[str] = Field(default_factory=lambda: ["digits", "punctuation", "whitespace"])


class ReviewConfig(_Section):
    enabled: bool = True
    flag_threshold: int = 55
    sample_fraction: float = 0.05
    min_coverage_for_export: float = 1.0


class BudgetConfig(_Section):
    max_usd: float = 10.0
    abort_on_exceed: bool = True
    warn_at_fraction: float = 0.8


class EpubExportConfig(_Section):
    rtl: bool = True
    embed_fonts: bool = True
    font_body: str = "Vazirmatn"
    endnotes_per_chapter: bool = True
    validate_with_epubcheck: bool = True


class DocxExportConfig(_Section):
    page: str = "A5"
    endnotes: bool = True


class MarkdownExportConfig(_Section):
    front_matter: bool = True


class BilingualHtmlConfig(_Section):
    include_source: bool = True


class ExportConfig(_Section):
    formats: list[str] = Field(default_factory=lambda: ["epub", "docx", "md", "txt"])
    pdf: bool = False
    bilingual: bool = False
    epub: EpubExportConfig = Field(default_factory=EpubExportConfig)
    docx: DocxExportConfig = Field(default_factory=DocxExportConfig)
    markdown: MarkdownExportConfig = Field(default_factory=MarkdownExportConfig)
    bilingual_html: BilingualHtmlConfig = Field(default_factory=BilingualHtmlConfig)


class EvalConfig(_Section):
    golden_dir: str = "tests/golden"
    calibration_dir: str = "eval/calibration"
    judge_model: str = "gpt-6-luna"
    samples_per_item: int = 3
    temperature: float = 0.2


class LoggingConfig(_Section):
    level: str = "INFO"
    json_logs: bool = True
    console: str = "rich"


class ConcurrencyConfig(_Section):
    parallel_chapters: int = 1


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SHAHRZAD_",
        env_nested_delimiter="__",
        extra="forbid",
        case_sensitive=False,
    )

    project: ProjectConfig = Field(default_factory=ProjectConfig)
    book: BookConfig = Field(default_factory=BookConfig)
    provider: ProviderConfig = Field(default_factory=ProviderConfig)
    glossary: GlossaryConfig = Field(default_factory=GlossaryConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    persian: PersianConfig = Field(default_factory=PersianConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    eval: EvalConfig = Field(default_factory=EvalConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must contain a mapping")
    return data


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def defaults_path(root: Path | None = None) -> Path:
    return project_root(root) / "configs" / "default.yaml"


def _parse_env_value(raw: str) -> Any:
    """Conservative env parsing: never turn 'off'/'on'/'yes'/'no' into booleans."""
    text = raw.strip()
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"none", "null"}:
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    if text.startswith(("[", "{")):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


def _env_overrides(prefix: str = "SHAHRZAD_") -> dict[str, Any]:
    """SHAHRZAD_BUDGET__MAX_USD=3 -> {"budget": {"max_usd": 3}}; SHAHRZAD_HOME excluded."""
    result: dict[str, Any] = {}
    for key, raw in os.environ.items():
        if not key.startswith(prefix) or key == f"{prefix}HOME":
            continue
        parts = key[len(prefix) :].lower().split("__")
        if not all(parts):
            continue
        cursor = result
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
        cursor[parts[-1]] = _parse_env_value(raw)
    return result


_DOTENV_LOADED: set[Path] = set()


def ensure_dotenv(root: Path | None = None) -> None:
    """Load one .env per project root, never overriding real environment variables."""
    path = project_root(root) / ".env"
    resolved = path.resolve()
    if resolved in _DOTENV_LOADED:
        return
    if path.is_file():
        load_dotenv(path, override=False)
    _DOTENV_LOADED.add(resolved)


def load_settings(
    *,
    root: Path | None = None,
    slug: str | None = None,
    overrides: dict[str, Any] | None = None,
    books_dir_name: str | None = None,
) -> Settings:
    """Load defaults -> book config -> env -> CLI overrides. Unknown keys are rejected."""
    base = project_root(root)
    ensure_dotenv(base)
    data = _read_yaml(defaults_path(base))
    if slug:
        data = _deep_merge(
            data, _read_yaml(book_dir(slug, base, books_dir_name) / "config.yaml")
        )
    data = _deep_merge(data, _env_overrides())
    if overrides:
        data = _deep_merge(data, overrides)
    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration:\n{exc}") from exc


def model_profile(settings: Settings, root: Path | None = None) -> ModelProfile:
    path = resolve_config_path(settings.provider.models, root)
    if path is None:
        raise ConfigError("provider.models is not set")
    return load_model_profile(path)


def redacted_config(settings: Settings) -> dict[str, Any]:
    """Config snapshot safe for manifests: no secrets can appear by construction."""
    data = settings.model_dump()
    for key in ("api_key", "key", "token", "secret", "password"):
        for section in data.values():
            if isinstance(section, dict) and key in section:
                section[key] = "<redacted>"
    return data


def api_key_for(provider: str, root: Path | None = None) -> str | None:
    ensure_dotenv(root)
    names = {
        "deepseek": ("DEEPSEEK_API_KEY",),
        "openrouter": ("OPENROUTER_API_KEY",),
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY",),
    }.get(provider, (f"{provider.upper().replace('-', '_')}_API_KEY",))
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None
