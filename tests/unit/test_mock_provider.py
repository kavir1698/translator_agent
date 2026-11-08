from __future__ import annotations

import json
from pathlib import Path

import pytest

from shahrzad.core.errors import MissingFixture
from shahrzad.core.models import TranslatorOutput
from shahrzad.llm.mock_provider import MockProvider, MockProviderFailure, fixture_key
from shahrzad.llm.provider import Message
from shahrzad.llm.schema import build_response_schema


def _messages() -> list[Message]:
    return [Message(role="system", content="translate"), Message(role="user", content="hello")]


def test_scripted_response():
    provider = MockProvider(script=[{"segments": [], "window_notes": ""}])
    result = provider.complete(
        messages=_messages(), model="mock", temperature=0.5, max_output_tokens=100
    )
    assert result.parsed == {"segments": [], "window_notes": ""}
    assert result.tokens_out > 0
    assert result.cost_usd == 0.0


def test_missing_fixture_fails_loudly(tmp_path: Path):
    provider = MockProvider(fixtures_dir=tmp_path)
    with pytest.raises(MissingFixture):
        provider.complete(messages=_messages(), model="mock", temperature=0.5, max_output_tokens=100)


def test_fixture_replay_and_failure_injection(tmp_path: Path):
    messages = _messages()
    schema = build_response_schema(TranslatorOutput)
    key = fixture_key(model="mock", temperature=0.5, messages=messages, response_schema=schema)
    folder = tmp_path / "p05_translator"
    folder.mkdir()
    (folder / f"{key}.json").write_text(
        json.dumps(
            {
                "agent": "A5",
                "prompt_id": "p05_translator",
                "request_hash": key,
                "fail_times": 1,
                "failure": "invalid_json",
                "responses": [{"segments": [], "window_notes": "ok"}],
            }
        ),
        encoding="utf-8",
    )
    provider = MockProvider(fixtures_dir=tmp_path)
    with pytest.raises(MockProviderFailure):
        provider.complete(
            messages=messages, model="mock", temperature=0.5, max_output_tokens=100, response_schema=schema
        )
    result = provider.complete(
        messages=messages, model="mock", temperature=0.5, max_output_tokens=100, response_schema=schema
    )
    assert result.parsed == {"segments": [], "window_notes": "ok"}
