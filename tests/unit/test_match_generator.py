from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from common.errors import ConfigError
from orchestrator.match_config import MatchConfig
from orchestrator.match_generator import build_match_config_dict

_N2 = 2
_N8 = 8
_N17 = 17
_DEFAULT_HEARTBEAT = 120
_DEFAULT_GRACE = 30
_DEFAULT_MAX = 1800
_DEFAULT_ARCHIVE = 60


def _write_agent(base: Path, name: str) -> Path:
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
                "system_prompt_path": "p.txt",
            }
        ),
        encoding="utf-8",
    )
    return p


def _agents(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], payload["agents"])


def test_1v1_assigns_slots_and_round_trips(tmp_path: Path) -> None:
    a0 = _write_agent(tmp_path, "a0.json")
    a1 = _write_agent(tmp_path, "a1.json")

    payload = build_match_config_dict(agent_paths=[a0, a1], match_id="m")

    assert payload["n_agents"] == _N2
    assert [a["slot"] for a in _agents(payload)] == list(range(_N2))
    assert [a["user"] for a in _agents(payload)] == [f"agent{i}" for i in range(_N2)]
    cfg = MatchConfig.model_validate(payload)
    assert cfg.n_agents == _N2


def test_8_agents_round_trips(tmp_path: Path) -> None:
    paths = [_write_agent(tmp_path, f"a{i}.json") for i in range(_N8)]

    payload = build_match_config_dict(agent_paths=paths, match_id="ffa8")

    cfg = MatchConfig.model_validate(payload)
    assert cfg.n_agents == _N8
    assert [a.slot for a in cfg.agents] == list(range(_N8))
    assert [a.user for a in cfg.agents] == [f"agent{i}" for i in range(_N8)]


def test_too_few_agents_raises(tmp_path: Path) -> None:
    a0 = _write_agent(tmp_path, "a0.json")
    with pytest.raises(ValueError, match="2"):
        build_match_config_dict(agent_paths=[a0], match_id="m")


def test_too_many_agents_raises(tmp_path: Path) -> None:
    paths = [_write_agent(tmp_path, f"a{i}.json") for i in range(_N17)]
    with pytest.raises(ValueError, match="16"):
        build_match_config_dict(agent_paths=paths, match_id="m")


def test_unreadable_agent_raises_config_error(tmp_path: Path) -> None:
    a0 = _write_agent(tmp_path, "a0.json")
    missing = tmp_path / "missing.json"
    with pytest.raises(ConfigError):
        build_match_config_dict(agent_paths=[a0, missing], match_id="m")


def test_defaults_match_documented_values(tmp_path: Path) -> None:
    a0 = _write_agent(tmp_path, "a0.json")
    a1 = _write_agent(tmp_path, "a1.json")

    payload = build_match_config_dict(agent_paths=[a0, a1], match_id="m")

    assert payload["heartbeat_interval_s"] == _DEFAULT_HEARTBEAT
    assert payload["grace_period_s"] == _DEFAULT_GRACE
    assert payload["max_duration_s"] == _DEFAULT_MAX
    assert payload["archive_grace_s"] == _DEFAULT_ARCHIVE
    assert payload["network_policy"] == "allowlist"
    assert payload["cgroup_limits"] is None
