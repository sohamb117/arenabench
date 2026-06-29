import json
import pathlib
from typing import cast

import pytest
from typer.testing import CliRunner

from orchestrator.cli import app

_HEARTBEAT = 120
_GRACE = 30
_MAX_DURATION = 1800
_ARCHIVE_GRACE = 60
_NOT_YET_WIRED_EXIT = 2


def _valid_match_payload(n_agents: int = 2) -> dict[str, object]:
    return {
        "match_id": "demo-1v1",
        "n_agents": n_agents,
        "heartbeat_interval_s": _HEARTBEAT,
        "grace_period_s": _GRACE,
        "max_duration_s": _MAX_DURATION,
        "archive_grace_s": _ARCHIVE_GRACE,
        "network_policy": "allowlist",
        "cgroup_limits": None,
        "agents": [
            {"slot": i, "user": f"agent{i}", "config": f"c{i}.json"} for i in range(n_agents)
        ],
    }


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_validate_accepts_valid_config(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    path = tmp_path / "match.json"
    path.write_text(json.dumps(_valid_match_payload()), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path)])

    assert result.exit_code == 0
    assert "OK match_id=demo-1v1" in result.stdout
    assert "n_agents=2" in result.stdout


def test_validate_rejects_bad_config(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    payload = _valid_match_payload()
    payload["n_agents"] = 20
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path)])

    assert result.exit_code == 1


def test_validate_rejects_negative_heartbeat(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    payload = _valid_match_payload()
    payload["heartbeat_interval_s"] = -1
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = runner.invoke(app, ["validate", str(path)])

    assert result.exit_code == 1


def test_validate_missing_file_fails(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    result = runner.invoke(app, ["validate", str(tmp_path / "absent.json")])
    assert result.exit_code == 1


def test_replay_pretty_prints_summary(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    match_dir = tmp_path / "match-demo"
    match_dir.mkdir()
    summary = {"result": "victory", "winner": 0, "cause": "opponent_crashed"}
    (match_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    result = runner.invoke(app, ["replay", str(match_dir)])

    assert result.exit_code == 0
    parsed = cast(object, json.loads(result.stdout))
    assert parsed == summary


def test_replay_missing_summary_fails(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    result = runner.invoke(app, ["replay", str(tmp_path)])
    assert result.exit_code == 1


def test_run_command_reports_not_wired(runner: CliRunner, tmp_path: pathlib.Path) -> None:
    path = tmp_path / "match.json"
    path.write_text(json.dumps(_valid_match_payload()), encoding="utf-8")

    result = runner.invoke(app, ["run", str(path)])

    assert result.exit_code == _NOT_YET_WIRED_EXIT


def test_build_vm_reports_not_wired(runner: CliRunner) -> None:
    result = runner.invoke(app, ["build-vm"])
    assert result.exit_code == _NOT_YET_WIRED_EXIT


def test_help_text_lists_all_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["validate", "replay", "run", "build-vm"]:
        assert cmd in result.stdout
