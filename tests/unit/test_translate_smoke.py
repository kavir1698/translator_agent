from __future__ import annotations

from pathlib import Path

from shahrzad.core.config import load_settings
from shahrzad.core.cost import load_model_profile
from shahrzad.pipeline.qa_pass import run_qa
from shahrzad.pipeline.translate_pass import TranslateOptions, run_translate

from .support import SmartProvider, make_workspace


def test_chapter_one_zero_config(isolated_home: Path, fixtures: dict[str, Path]) -> None:
    make_workspace(isolated_home, fixtures)
    settings = load_settings(root=isolated_home)
    profile = load_model_profile(isolated_home / "configs/models/deepseek.yaml")
    provider = SmartProvider()
    result = run_translate(
        root=isolated_home,
        slug="test-book",
        settings=settings,
        profile=profile,
        provider=provider,
        options=TranslateOptions(chapters=[1], cost_mode="standard", max_usd=10),
    )
    assert result.coverage > 0
    assert result.report_path is not None and result.report_path.is_file()
    qa = run_qa(root=isolated_home, slug="test-book", settings=settings)
    assert qa.flags_path is not None and qa.flags_path.is_file()
