import pathlib

import pytest

from orchestrator.match_config import MatchConfig, load_match_config
from tests.unit._match_config_helpers import (
    CPU_QUOTA_MS_PER_S,
    MEM_MB,
    N_AGENTS_4,
    NETWORK_POLICY_FULL,
    SLOT_0,
    SLOT_1,
    SLOT_2,
    SLOT_3,
    valid_2_agent_payload,
    valid_4_agent_payload,
    write_match,
)

BUDGET_USD = 12.5
ONE_YEAR_S = 31_536_000


def test_valid_2_agent_config_parses_and_round_trips(tmp_path: pathlib.Path) -> None:
    path = write_match(tmp_path, valid_2_agent_payload())

    cfg = load_match_config(path)

    assert cfg.model_dump(mode="json") == valid_2_agent_payload()
    assert MatchConfig.model_validate(cfg.model_dump(mode="json")) == cfg


def test_null_max_duration_loads_as_one_year(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["max_duration_s"] = None

    cfg = load_match_config(write_match(tmp_path, payload))

    assert cfg.max_duration_s == ONE_YEAR_S
    assert type(cfg.max_duration_s) is int


@pytest.mark.parametrize("duration_s", [86_400, ONE_YEAR_S])
def test_explicit_max_durations_through_one_year_are_preserved(
    tmp_path: pathlib.Path, duration_s: int
) -> None:
    payload = valid_2_agent_payload()
    payload["max_duration_s"] = duration_s

    cfg = load_match_config(write_match(tmp_path, payload, f"match-{duration_s}.json"))

    assert cfg.max_duration_s == duration_s


def test_domain_allowlist_extra_defaults_none(tmp_path: pathlib.Path) -> None:
    cfg = load_match_config(write_match(tmp_path, valid_2_agent_payload()))
    assert cfg.domain_allowlist_extra is None


def test_budget_caps_default_none_and_are_independent(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["budget_usd"] = BUDGET_USD

    cfg = load_match_config(write_match(tmp_path, payload))

    assert cfg.budget_usd == BUDGET_USD
    assert cfg.per_agent_budget_usd is None


def test_absent_budget_keys_can_remain_omitted() -> None:
    cfg = MatchConfig.model_validate(valid_2_agent_payload())

    dumped = cfg.model_dump(mode="json", exclude_none=True)

    assert "budget_usd" not in dumped
    assert "per_agent_budget_usd" not in dumped


def test_domain_allowlist_extra_parses_list_to_tuple(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["domain_allowlist_extra"] = [" API.Custom.AI ", "llm.internal.corp"]
    cfg = load_match_config(write_match(tmp_path, payload, "extra.json"))
    assert cfg.domain_allowlist_extra == ("api.custom.ai", "llm.internal.corp")


def test_valid_4_agent_config_parses_with_slots_0_to_3(tmp_path: pathlib.Path) -> None:
    path = write_match(tmp_path, valid_4_agent_payload())

    cfg = load_match_config(path)

    assert cfg.n_agents == N_AGENTS_4
    assert [a.slot for a in cfg.agents] == [SLOT_0, SLOT_1, SLOT_2, SLOT_3]


def test_network_policy_full_parses(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["network_policy"] = NETWORK_POLICY_FULL

    cfg = load_match_config(write_match(tmp_path, payload))

    assert cfg.network_policy == NETWORK_POLICY_FULL


def test_cgroup_limits_none_and_valid_values_parse(tmp_path: pathlib.Path) -> None:
    payload = valid_2_agent_payload()
    payload["cgroup_limits"] = None
    cfg_none = load_match_config(write_match(tmp_path, payload, "match-none.json"))
    assert cfg_none.cgroup_limits is None

    payload_with_limits = valid_2_agent_payload()
    payload_with_limits["cgroup_limits"] = {
        "cpu_quota_ms_per_s": CPU_QUOTA_MS_PER_S,
        "mem_mb": MEM_MB,
    }
    cfg_limits = load_match_config(write_match(tmp_path, payload_with_limits, "match-limits.json"))
    assert cfg_limits.cgroup_limits is not None
    assert cfg_limits.cgroup_limits.cpu_quota_ms_per_s == CPU_QUOTA_MS_PER_S
    assert cfg_limits.cgroup_limits.mem_mb == MEM_MB
