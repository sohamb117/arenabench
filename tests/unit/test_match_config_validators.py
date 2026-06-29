import json
import pathlib

import pytest

from common.errors import ConfigError
from orchestrator.match_config import load_match_config

_MATCH_ID = "demo-1v1"
_N_AGENTS_2 = 2
_HEARTBEAT_INTERVAL_S = 120
_GRACE_PERIOD_S = 30
_MAX_DURATION_S = 1800
_ARCHIVE_GRACE_S = 60


def _payload_with_agent(slot: int, user: str) -> dict[str, object]:
    return {
        "match_id": _MATCH_ID,
        "n_agents": _N_AGENTS_2,
        "heartbeat_interval_s": _HEARTBEAT_INTERVAL_S,
        "grace_period_s": _GRACE_PERIOD_S,
        "max_duration_s": _MAX_DURATION_S,
        "archive_grace_s": _ARCHIVE_GRACE_S,
        "network_policy": "allowlist",
        "cgroup_limits": None,
        "agents": [
            {"slot": slot, "user": user, "config": "a.json"},
            {"slot": 1 - slot, "user": f"agent{1 - slot}", "config": "b.json"},
        ],
    }


def test_user_must_match_slot_rejects_mismatched_user(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "match.json"
    path.write_text(json.dumps(_payload_with_agent(0, "agent99")), encoding="utf-8")

    with pytest.raises(ConfigError):
        load_match_config(path)


def test_user_must_match_slot_accepts_matched_user(tmp_path: pathlib.Path) -> None:
    path = tmp_path / "match.json"
    path.write_text(json.dumps(_payload_with_agent(0, "agent0")), encoding="utf-8")

    cfg = load_match_config(path)

    assert cfg.agents[0].user == "agent0"
    assert cfg.agents[0].slot == 0
