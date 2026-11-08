from __future__ import annotations

import json

from typer.testing import CliRunner

from shahrzad.cli.main import app

runner = CliRunner()


def test_doctor_without_keys_exits_zero(isolated_home):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.stdout
    assert "api_key" in result.stdout


def test_doctor_json(isolated_home):
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["failed"] is False
    names = {check["name"] for check in payload["checks"]}
    assert {"python", "config", "profile", "prompts", "api_key"} <= names


def test_estimate_prints_tariffs(isolated_home):
    result = runner.invoke(app, ["estimate", "--words", "90000"])
    assert result.exit_code == 0, result.stdout
    assert "Cost by mode and tariff" in result.stdout
    assert "economy" in result.stdout
    assert "standard" in result.stdout
    assert "quality" in result.stdout


def test_estimate_json(isolated_home):
    result = runner.invoke(app, ["estimate", "--words", "90000", "--json"])
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["words"] == 90000
    assert set(payload["modes"]) == {"economy", "standard", "quality"}
    assert payload["modes"]["standard"]["peak_usd"] > 0
