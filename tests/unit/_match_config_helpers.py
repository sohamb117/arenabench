"""Shared payload builders for the match-config test modules.

Split out of test_match_config.py so both test_match_config.py (parse/happy
paths) and test_match_config_validators.py (rejection paths) stay within the
250-LOC cap while sharing one source of truth for the sample payloads.
"""

from __future__ import annotations

import json
import pathlib

MATCH_ID = "demo-1v1"
N_AGENTS_2 = 2
N_AGENTS_4 = 4
HEARTBEAT_INTERVAL_S = 120
GRACE_PERIOD_S = 30
MAX_DURATION_S = 1800
ARCHIVE_GRACE_S = 60
NETWORK_POLICY_ALLOWLIST = "allowlist"
NETWORK_POLICY_FULL = "full"
CPU_QUOTA_MS_PER_S = 1000
MEM_MB = 2048

SLOT_0 = 0
SLOT_1 = 1
SLOT_2 = 2
SLOT_3 = 3
SLOT_7 = 7


def agent(slot: int, user: str, config: str) -> dict[str, object]:
    return {"slot": slot, "user": user, "config": config}


def base_payload(
    *,
    n_agents: int,
    agents: list[dict[str, object]],
    network_policy: str = NETWORK_POLICY_ALLOWLIST,
    cgroup_limits: dict[str, int] | None = None,
    domain_allowlist_extra: list[str] | None = None,
) -> dict[str, object]:
    return {
        "match_id": MATCH_ID,
        "n_agents": n_agents,
        "heartbeat_interval_s": HEARTBEAT_INTERVAL_S,
        "grace_period_s": GRACE_PERIOD_S,
        "max_duration_s": MAX_DURATION_S,
        "archive_grace_s": ARCHIVE_GRACE_S,
        "network_policy": network_policy,
        "cgroup_limits": cgroup_limits,
        "domain_allowlist_extra": domain_allowlist_extra,
        "agents": agents,
    }


def write_match(
    tmp_path: pathlib.Path,
    payload: dict[str, object],
    name: str = "match.json",
) -> pathlib.Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def valid_2_agent_payload() -> dict[str, object]:
    return base_payload(
        n_agents=N_AGENTS_2,
        agents=[
            agent(SLOT_0, "agent0", "configs/agents/claude.json"),
            agent(SLOT_1, "agent1", "configs/agents/gpt.json"),
        ],
    )


def valid_4_agent_payload() -> dict[str, object]:
    return base_payload(
        n_agents=N_AGENTS_4,
        agents=[
            agent(SLOT_0, "agent0", "configs/agents/a.json"),
            agent(SLOT_1, "agent1", "configs/agents/b.json"),
            agent(SLOT_2, "agent2", "configs/agents/c.json"),
            agent(SLOT_3, "agent3", "configs/agents/d.json"),
        ],
    )
