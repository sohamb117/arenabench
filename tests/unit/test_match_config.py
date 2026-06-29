import json
import pathlib

import pytest

from common.errors import ConfigError
from orchestrator.match_config import MatchConfig, load_match_config

_MATCH_ID = "demo-1v1"
_N_AGENTS_2 = 2
_N_AGENTS_4 = 4
_HEARTBEAT_INTERVAL_S = 120
_GRACE_PERIOD_S = 30
_MAX_DURATION_S = 1800
_ARCHIVE_GRACE_S = 60
_NETWORK_POLICY_ALLOWLIST = "allowlist"
_NETWORK_POLICY_FULL = "full"
_CPU_QUOTA_MS_PER_S = 1000
_MEM_MB = 2048

_SLOT_0 = 0
_SLOT_1 = 1
_SLOT_2 = 2
_SLOT_3 = 3
_SLOT_7 = 7


def _agent(slot: int, user: str, config: str) -> dict[str, object]:
    return {"slot": slot, "user": user, "config": config}


def _base_payload(
    *,
    n_agents: int,
    agents: list[dict[str, object]],
    network_policy: str = _NETWORK_POLICY_ALLOWLIST,
    cgroup_limits: dict[str, int] | None = None,
) -> dict[str, object]:
    return {
        "match_id": _MATCH_ID,
        "n_agents": n_agents,
        "heartbeat_interval_s": _HEARTBEAT_INTERVAL_S,
        "grace_period_s": _GRACE_PERIOD_S,
        "max_duration_s": _MAX_DURATION_S,
        "archive_grace_s": _ARCHIVE_GRACE_S,
        "network_policy": network_policy,
        "cgroup_limits": cgroup_limits,
        "agents": agents,
    }


def _write_match(
    tmp_path: pathlib.Path,
    payload: dict[str, object],
    name: str = "match.json",
) -> pathlib.Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _valid_2_agent_payload() -> dict[str, object]:
    return _base_payload(
        n_agents=_N_AGENTS_2,
        agents=[
            _agent(_SLOT_0, "agent0", "configs/agents/claude-sonnet.json"),
            _agent(_SLOT_1, "agent1", "configs/agents/gpt-4o.json"),
        ],
    )


def _valid_4_agent_payload() -> dict[str, object]:
    return _base_payload(
        n_agents=_N_AGENTS_4,
        agents=[
            _agent(_SLOT_0, "agent0", "configs/agents/a.json"),
            _agent(_SLOT_1, "agent1", "configs/agents/b.json"),
            _agent(_SLOT_2, "agent2", "configs/agents/c.json"),
            _agent(_SLOT_3, "agent3", "configs/agents/d.json"),
        ],
    )


def test_valid_2_agent_config_parses_and_round_trips(tmp_path: pathlib.Path) -> None:
    # Given
    path = _write_match(tmp_path, _valid_2_agent_payload())

    # When
    cfg = load_match_config(path)

    # Then
    assert cfg.model_dump(mode="json") == _valid_2_agent_payload()
    assert MatchConfig.model_validate(cfg.model_dump(mode="json")) == cfg


def test_valid_4_agent_config_parses_with_slots_0_to_3(tmp_path: pathlib.Path) -> None:
    # Given
    path = _write_match(tmp_path, _valid_4_agent_payload())

    # When
    cfg = load_match_config(path)

    # Then
    assert cfg.n_agents == _N_AGENTS_4
    assert [agent.slot for agent in cfg.agents] == [
        _SLOT_0,
        _SLOT_1,
        _SLOT_2,
        _SLOT_3,
    ]


def test_n_agents_1_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = _valid_2_agent_payload()
    payload["n_agents"] = 1
    path = _write_match(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_match_config(path)


def test_n_agents_20_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = _valid_2_agent_payload()
    payload["n_agents"] = 20
    path = _write_match(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_match_config(path)


def test_heartbeat_interval_negative_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = _valid_2_agent_payload()
    payload["heartbeat_interval_s"] = -1
    path = _write_match(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_match_config(path)


def test_agents_len_mismatch_raises_config_error_with_agents_field(tmp_path: pathlib.Path) -> None:
    # Given
    payload = _base_payload(
        n_agents=_N_AGENTS_2,
        agents=[_agent(_SLOT_0, "agent0", "configs/agents/a.json")],
    )
    path = _write_match(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError) as exc_info:
        load_match_config(path)

    assert exc_info.value.field == "agents"


def test_duplicate_slot_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = _valid_2_agent_payload()
    payload["agents"] = [
        _agent(_SLOT_0, "agent0", "configs/agents/claude-sonnet.json"),
        _agent(_SLOT_0, "agent1", "configs/agents/gpt-4o.json"),
    ]
    path = _write_match(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_match_config(path)


def test_duplicate_user_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = _valid_2_agent_payload()
    payload["agents"] = [
        _agent(_SLOT_0, "agent0", "configs/agents/claude-sonnet.json"),
        _agent(_SLOT_1, "agent0", "configs/agents/gpt-4o.json"),
    ]
    path = _write_match(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_match_config(path)


def test_slot_7_in_4_agent_config_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = _valid_4_agent_payload()
    payload["agents"] = [
        _agent(_SLOT_0, "agent0", "configs/agents/a.json"),
        _agent(_SLOT_1, "agent1", "configs/agents/b.json"),
        _agent(_SLOT_2, "agent2", "configs/agents/c.json"),
        _agent(_SLOT_7, "agent3", "configs/agents/d.json"),
    ]
    path = _write_match(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_match_config(path)


def test_network_policy_full_parses(tmp_path: pathlib.Path) -> None:
    # Given
    payload = _valid_2_agent_payload()
    payload["network_policy"] = _NETWORK_POLICY_FULL
    path = _write_match(tmp_path, payload)

    # When
    cfg = load_match_config(path)

    # Then
    assert cfg.network_policy == _NETWORK_POLICY_FULL


def test_cgroup_limits_none_and_valid_values_parse(tmp_path: pathlib.Path) -> None:
    """Plan §3.B13: cgroup_limits is OPTIONAL on MatchConfig. The field parses
    cleanly when null OR when set to valid bounds. Runtime enforcement (via a
    systemd-run wrapper around the SSH harness command) is a follow-up;
    the schema contract still accepts the config so user-side plumbing is unblocked.
    """
    payload = _valid_2_agent_payload()
    payload["cgroup_limits"] = None
    cfg_none = load_match_config(_write_match(tmp_path, payload, "match-none.json"))
    assert cfg_none.cgroup_limits is None

    payload_with_limits = _valid_2_agent_payload()
    payload_with_limits["cgroup_limits"] = {
        "cpu_quota_ms_per_s": _CPU_QUOTA_MS_PER_S,
        "mem_mb": _MEM_MB,
    }
    cfg_limits = load_match_config(_write_match(tmp_path, payload_with_limits, "match-limits.json"))
    assert cfg_limits.cgroup_limits is not None
    assert cfg_limits.cgroup_limits.cpu_quota_ms_per_s == _CPU_QUOTA_MS_PER_S
    assert cfg_limits.cgroup_limits.mem_mb == _MEM_MB


def test_missing_file_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    path = tmp_path / "missing.json"

    # When / Then
    with pytest.raises(ConfigError):
        load_match_config(path)
