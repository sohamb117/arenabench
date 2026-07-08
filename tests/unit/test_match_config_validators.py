import pathlib

import pytest

from common.errors import ConfigError
from orchestrator.match_config import load_match_config
from tests.unit._match_config_helpers import (
    N_AGENTS_2,
    SLOT_0,
    SLOT_1,
    SLOT_2,
    SLOT_7,
    agent,
    base_payload,
    valid_2_agent_payload,
    valid_4_agent_payload,
    write_match,
)


def test_user_must_match_slot_rejects_mismatched_user(tmp_path: pathlib.Path) -> None:
    payload = base_payload(
        n_agents=N_AGENTS_2,
        agents=[
            agent(SLOT_0, "agent99", "a.json"),
            agent(SLOT_1, "agent1", "b.json"),
        ],
    )
    with pytest.raises(ConfigError):
        load_match_config(write_match(tmp_path, payload))


def test_user_must_match_slot_accepts_matched_user(tmp_path: pathlib.Path) -> None:
    payload = base_payload(
        n_agents=N_AGENTS_2,
        agents=[
            agent(SLOT_0, "agent0", "a.json"),
            agent(SLOT_1, "agent1", "b.json"),
        ],
    )

    cfg = load_match_config(write_match(tmp_path, payload))

    assert cfg.agents[0].user == "agent0"
    assert cfg.agents[0].slot == 0


def test_domain_allowlist_extra_rejects_empty_entry(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["domain_allowlist_extra"] = ["ok.com", ""]
    with pytest.raises(ConfigError):
        load_match_config(write_match(tmp_path, payload, "bad.json"))


def test_n_agents_1_raises_config_error(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["n_agents"] = 1
    with pytest.raises(ConfigError):
        load_match_config(write_match(tmp_path, payload))


def test_n_agents_20_raises_config_error(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["n_agents"] = 20
    with pytest.raises(ConfigError):
        load_match_config(write_match(tmp_path, payload))


def test_heartbeat_interval_negative_raises_config_error(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["heartbeat_interval_s"] = -1
    with pytest.raises(ConfigError):
        load_match_config(write_match(tmp_path, payload))


def test_agents_len_mismatch_raises_config_error_with_agents_field(tmp_path: pathlib.Path) -> None:
    payload = base_payload(
        n_agents=N_AGENTS_2,
        agents=[agent(SLOT_0, "agent0", "configs/agents/a.json")],
    )
    with pytest.raises(ConfigError) as exc_info:
        load_match_config(write_match(tmp_path, payload))
    assert exc_info.value.field == "agents"


def test_duplicate_slot_raises_config_error(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["agents"] = [
        agent(SLOT_0, "agent0", "configs/agents/claude.json"),
        agent(SLOT_0, "agent1", "configs/agents/gpt.json"),
    ]
    with pytest.raises(ConfigError):
        load_match_config(write_match(tmp_path, payload))


def test_duplicate_user_raises_config_error(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["agents"] = [
        agent(SLOT_0, "agent0", "configs/agents/claude.json"),
        agent(SLOT_1, "agent0", "configs/agents/gpt.json"),
    ]
    with pytest.raises(ConfigError):
        load_match_config(write_match(tmp_path, payload))


def test_slot_7_in_4_agent_config_raises_config_error(tmp_path: pathlib.Path) -> None:
    payload = valid_4_agent_payload()
    payload["agents"] = [
        agent(SLOT_0, "agent0", "configs/agents/a.json"),
        agent(SLOT_1, "agent1", "configs/agents/b.json"),
        agent(SLOT_2, "agent2", "configs/agents/c.json"),
        agent(SLOT_7, "agent3", "configs/agents/d.json"),
    ]
    with pytest.raises(ConfigError):
        load_match_config(write_match(tmp_path, payload))


def test_missing_file_raises_config_error(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "missing.json"
    with pytest.raises(ConfigError):
        load_match_config(path)
