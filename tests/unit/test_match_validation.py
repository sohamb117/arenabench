from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.errors import ConfigError
from orchestrator.match_config import MatchConfig
from orchestrator.match_validation import check_referenced_files_exist

_HEARTBEAT = 120
_GRACE = 30
_MAX = 1800
_ARCHIVE = 60


def _agent_cfg_json(prompt_rel: str) -> str:
    return json.dumps(
        {
            "model": "openai/gpt-x",
            "temperature": 0.0,
            "max_tokens": 1000,
            "request_timeout_s": 60,
            "num_retries": 1,
            "parser": "json",
            "api_key_env": "OPENAI_API_KEY",
            "system_prompt_path": prompt_rel,
        }
    )


def _match(n: int) -> MatchConfig:
    return MatchConfig.model_validate(
        {
            "match_id": "t",
            "n_agents": n,
            "heartbeat_interval_s": _HEARTBEAT,
            "grace_period_s": _GRACE,
            "max_duration_s": _MAX,
            "archive_grace_s": _ARCHIVE,
            "network_policy": "allowlist",
            "cgroup_limits": None,
            "agents": [{"slot": i, "user": f"agent{i}", "config": f"a{i}.json"} for i in range(n)],
        }
    )


def _write_valid_agent(base: Path, slot: int) -> None:
    prompt_rel = f"p{slot}.txt"
    (base / f"a{slot}.json").write_text(_agent_cfg_json(prompt_rel), encoding="utf-8")
    (base / prompt_rel).write_text("stay alive\n", encoding="utf-8")


def test_all_files_present_passes(tmp_path: Path) -> None:
    _write_valid_agent(tmp_path, 0)
    _write_valid_agent(tmp_path, 1)
    check_referenced_files_exist(_match(2), tmp_path)


def test_missing_agent_config_raises(tmp_path: Path) -> None:
    _write_valid_agent(tmp_path, 0)
    (tmp_path / "p1.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        check_referenced_files_exist(_match(2), tmp_path)
    assert exc.value.field == "agents[1].config"
    assert "a1.json" in exc.value.path


def test_invalid_agent_config_raises(tmp_path: Path) -> None:
    _write_valid_agent(tmp_path, 0)
    (tmp_path / "a1.json").write_text("{ not valid json", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        check_referenced_files_exist(_match(2), tmp_path)
    assert exc.value.field == "agents[1].config"


def test_missing_system_prompt_raises(tmp_path: Path) -> None:
    _write_valid_agent(tmp_path, 0)
    (tmp_path / "a1.json").write_text(_agent_cfg_json("p1.txt"), encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        check_referenced_files_exist(_match(2), tmp_path)
    assert exc.value.field == "agents[1].system_prompt_path"


def test_empty_system_prompt_raises(tmp_path: Path) -> None:
    (tmp_path / "a0.json").write_text(_agent_cfg_json("p0.txt"), encoding="utf-8")
    (tmp_path / "p0.txt").write_text("   \n", encoding="utf-8")
    with pytest.raises(ConfigError) as exc:
        check_referenced_files_exist(_match(2), tmp_path)
    assert exc.value.field == "agents[0].system_prompt_path"


def test_large_n_pinpoints_missing_agent(tmp_path: Path) -> None:
    _write_valid_agent(tmp_path, 0)
    _write_valid_agent(tmp_path, 1)
    _write_valid_agent(tmp_path, 3)
    with pytest.raises(ConfigError) as exc:
        check_referenced_files_exist(_match(4), tmp_path)
    assert exc.value.field == "agents[2].config"
