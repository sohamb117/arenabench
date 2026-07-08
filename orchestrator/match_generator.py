from __future__ import annotations

from pathlib import Path

from harness.config import load_config

_MIN_AGENTS = 2
_MAX_AGENTS = 16

_DEFAULT_HEARTBEAT_S = 120
_DEFAULT_GRACE_S = 30
_DEFAULT_MAX_DURATION_S = 1800
_DEFAULT_ARCHIVE_GRACE_S = 60


def build_match_config_dict(
    *,
    agent_paths: list[Path],
    match_id: str,
    heartbeat_interval_s: int = _DEFAULT_HEARTBEAT_S,
    grace_period_s: int = _DEFAULT_GRACE_S,
    max_duration_s: int = _DEFAULT_MAX_DURATION_S,
    archive_grace_s: int = _DEFAULT_ARCHIVE_GRACE_S,
    network_policy: str = "allowlist",
    domain_allowlist_extra: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """Build a match-config dict from an ordered list of agent config paths.

    Slot ``i`` and user ``agent{i}`` are assigned in input order, so callers
    never hand-manage the slot/user contiguity the schema requires. Each agent
    path is loaded as an AgentConfig first, so a bad or missing agent config
    fails here (ConfigError) rather than later. The returned dict round-trips
    through MatchConfig; the caller is expected to model_validate it before use.
    """
    n = len(agent_paths)
    if not _MIN_AGENTS <= n <= _MAX_AGENTS:
        raise ValueError(f"a match needs between {_MIN_AGENTS} and {_MAX_AGENTS} agents, got {n}")
    agents: list[dict[str, object]] = []
    for slot, path in enumerate(agent_paths):
        _ = load_config(path)
        agents.append({"slot": slot, "user": f"agent{slot}", "config": str(path)})
    payload: dict[str, object] = {
        "match_id": match_id,
        "n_agents": n,
        "heartbeat_interval_s": heartbeat_interval_s,
        "grace_period_s": grace_period_s,
        "max_duration_s": max_duration_s,
        "archive_grace_s": archive_grace_s,
        "network_policy": network_policy,
        "cgroup_limits": None,
        "agents": agents,
    }
    if domain_allowlist_extra is not None:
        payload["domain_allowlist_extra"] = list(domain_allowlist_extra)
    return payload
