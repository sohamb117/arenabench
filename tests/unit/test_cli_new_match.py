import json
import pathlib
from typing import cast

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app
from orchestrator.match_config import MatchConfig

_N2 = 2
_BUDGET_USD = 12.5
_PER_AGENT_BUDGET_USD = 7.25


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _write_agent(
    base: pathlib.Path,
    name: str,
    system_prompt_path: str = "configs/prompts/adversarial.txt",
) -> pathlib.Path:
    p = base / name
    p.write_text(
        json.dumps(
            {
                "model": "openai/gpt-x",
                "temperature": 0.0,
                "max_tokens": 1000,
                "request_timeout_s": 60,
                "num_retries": 1,
                "parser": "json",
                "api_key_env": "OPENAI_API_KEY",
                "system_prompt_path": system_prompt_path,
            }
        ),
        encoding="utf-8",
    )
    return p


def test_new_match_emits_valid_json_to_stdout(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    a0 = _write_agent(tmp_path, "a0.json")
    a1 = _write_agent(tmp_path, "a1.json")

    result = runner.invoke(app, ["new-match", "-a", str(a0), "-a", str(a1), "--match-id", "qa"])

    assert result.exit_code == 0
    payload = cast(dict[str, object], json.loads(result.stdout))
    cfg = MatchConfig.model_validate(payload)
    assert cfg.n_agents == _N2
    assert cfg.match_id == "qa"
    assert "budget_usd" not in payload
    assert "per_agent_budget_usd" not in payload


def test_new_match_emits_requested_budget_caps(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    a0 = _write_agent(tmp_path, "a0.json")
    a1 = _write_agent(tmp_path, "a1.json")

    result = runner.invoke(
        app,
        [
            "new-match",
            "-a",
            str(a0),
            "-a",
            str(a1),
            "--match-id",
            "qa",
            "--budget-usd",
            "12.5",
            "--per-agent-budget-usd",
            "7.25",
        ],
    )

    assert result.exit_code == 0
    payload = cast(dict[str, object], json.loads(result.stdout))
    assert payload["budget_usd"] == _BUDGET_USD
    assert payload["per_agent_budget_usd"] == _PER_AGENT_BUDGET_USD


def test_new_match_writes_out_file(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    a0 = _write_agent(tmp_path, "a0.json")
    a1 = _write_agent(tmp_path, "a1.json")
    out = tmp_path / "sub" / "match.json"

    result = runner.invoke(
        app, ["new-match", "-a", str(a0), "-a", str(a1), "--match-id", "qa", "-o", str(out)]
    )

    assert result.exit_code == 0
    assert out.is_file()
    cfg = MatchConfig.model_validate_json(out.read_text(encoding="utf-8"))
    assert cfg.n_agents == _N2


def test_new_match_rejects_bad_match_id(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    a0 = _write_agent(tmp_path, "a0.json")
    a1 = _write_agent(tmp_path, "a1.json")

    result = runner.invoke(app, ["new-match", "-a", str(a0), "-a", str(a1), "--match-id", "bad id"])

    assert result.exit_code == 1
    assert "match_id" in (result.output + (result.stderr or ""))


def test_new_match_reports_missing_agent(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    a0 = _write_agent(tmp_path, "a0.json")
    missing = tmp_path / "missing.json"

    result = runner.invoke(
        app, ["new-match", "-a", str(a0), "-a", str(missing), "--match-id", "qa"]
    )

    assert result.exit_code == 1
    assert "missing.json" in (result.output + (result.stderr or ""))


def test_new_match_rejects_missing_system_prompt(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    a0 = _write_agent(tmp_path, "a0.json", system_prompt_path="configs/prompts/does-not-exist.txt")
    a1 = _write_agent(tmp_path, "a1.json")

    result = runner.invoke(app, ["new-match", "-a", str(a0), "-a", str(a1), "--match-id", "qa"])

    assert result.exit_code == 1
    assert "system_prompt_path" in (result.output + (result.stderr or ""))
