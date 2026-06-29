import json
import pathlib
import re
import shutil
from typing import cast

import pytest
import yaml

from common.errors import ConfigError
from orchestrator.cloudinit import render_user_data, write_seed_iso
from orchestrator.match_config import MatchConfig

_HEARTBEAT = 120
_GRACE = 30
_MAX_DURATION = 1800
_ARCHIVE_GRACE = 60
_CPU_QUOTA = 500
_MEM_MB = 1024
_TEST_SSH_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItestkey arenabench-test"
_EXPECTED_WRITE_FILES_PER_AGENT = 3
_AGENT_COUNT_4 = 4
_AGENT_COUNT_2 = 2


def _match_config(
    *,
    n_agents: int,
    network_policy: str = "allowlist",
    cgroup_limits: dict[str, int] | None = None,
) -> MatchConfig:
    payload: dict[str, object] = {
        "match_id": "demo-test",
        "n_agents": n_agents,
        "heartbeat_interval_s": _HEARTBEAT,
        "grace_period_s": _GRACE,
        "max_duration_s": _MAX_DURATION,
        "archive_grace_s": _ARCHIVE_GRACE,
        "network_policy": network_policy,
        "cgroup_limits": cgroup_limits,
        "agents": [
            {"slot": i, "user": f"agent{i}", "config": f"configs/agents/a{i}.json"}
            for i in range(n_agents)
        ],
    }
    return MatchConfig.model_validate(payload)


def _blobs(n: int) -> tuple[dict[int, str], dict[int, str]]:
    config_blobs = {i: json.dumps({"slot": i, "model": "test"}) for i in range(n)}
    prompt_blobs = {i: f"prompt for agent{i}\n" for i in range(n)}
    return config_blobs, prompt_blobs


def test_happy_2_agents_renders_users_and_lingers() -> None:
    cfg = _match_config(n_agents=2)
    cb, pb = _blobs(2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    assert out.startswith("#cloud-config")
    assert "name: agent0" in out
    assert "name: agent1" in out
    assert "loginctl enable-linger agent0" in out
    assert "loginctl enable-linger agent1" in out
    assert "arenabench-harness.service" in out
    assert _TEST_SSH_PUBKEY in out
    assert "ssh_pwauth: false" in out
    assert "name: root" in out


def test_happy_4_agents_all_blocks_present() -> None:
    cfg = _match_config(n_agents=4)
    cb, pb = _blobs(4)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    for i in range(4):
        assert f"name: agent{i}" in out
        assert f"loginctl enable-linger agent{i}" in out


def test_network_policy_full_flushes_iptables() -> None:
    cfg = _match_config(n_agents=2, network_policy="full")
    cb, pb = _blobs(2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    assert "iptables -F" in out
    assert "iptables -P INPUT ACCEPT" in out


def test_network_policy_allowlist_no_flush() -> None:
    cfg = _match_config(n_agents=2)
    cb, pb = _blobs(2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    assert "iptables -F" not in out


def test_cgroup_limits_emits_drop_in() -> None:
    cfg = _match_config(
        n_agents=2,
        cgroup_limits={"cpu_quota_ms_per_s": _CPU_QUOTA, "mem_mb": _MEM_MB},
    )
    cb, pb = _blobs(2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    assert f"CPUQuota={_CPU_QUOTA}ms/s" in out
    assert f"MemoryMax={_MEM_MB}M" in out


def test_no_cgroup_limits_no_drop_in() -> None:
    cfg = _match_config(n_agents=2)
    cb, pb = _blobs(2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    assert "CPUQuota" not in out
    assert "MemoryMax" not in out


def test_missing_prompt_blob_raises_config_error() -> None:
    cfg = _match_config(n_agents=2)
    cb = {0: '{"slot":0}', 1: '{"slot":1}'}
    pb = {0: "prompt0"}

    with pytest.raises(ConfigError) as exc_info:
        render_user_data(
            match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
        )

    assert exc_info.value.field == "prompt_blobs[1]"


def test_missing_config_blob_raises_config_error() -> None:
    cfg = _match_config(n_agents=2)
    cb = {0: '{"slot":0}'}
    pb = {0: "p0", 1: "p1"}

    with pytest.raises(ConfigError) as exc_info:
        render_user_data(
            match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
        )

    assert exc_info.value.field == "config_blobs[1]"


@pytest.mark.skipif(
    shutil.which("cloud-localds") is None,
    reason="cloud-localds not installed on host",
)
def test_seed_iso_smoke(tmp_path: pathlib.Path) -> None:
    cfg = _match_config(n_agents=2)
    cb, pb = _blobs(2)
    user_data = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )
    out_iso = tmp_path / "seed.iso"

    write_seed_iso(user_data=user_data, out_path=out_iso)

    assert out_iso.exists()
    assert out_iso.stat().st_size > 0


def test_rendered_yaml_parses_and_structure_is_correct() -> None:
    """Round-4 lock: Jinja whitespace must not collapse YAML list items.

    Catches the collapse pattern `":  -"` directly AND parses the rendered
    YAML with PyYAML to ensure cloud-init can ingest it.
    """
    cfg = _match_config(n_agents=_AGENT_COUNT_2)
    cb, pb = _blobs(_AGENT_COUNT_2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    assert not re.search(r":[ \t]{2,}-[ \t]", out), (
        f"YAML list item collapsed onto map key line:\n{out}"
    )
    assert not re.search(r'"[ \t]+-[ \t]+name:', out), (
        f"sequence item collapsed after quoted scalar:\n{out}"
    )

    parsed = cast(object, yaml.safe_load(out))
    assert isinstance(parsed, dict)
    doc = cast(dict[str, object], parsed)
    users = cast(list[dict[str, object]], doc["users"])
    assert len(users) == _AGENT_COUNT_2 + 1
    assert users[0]["name"] == "root"
    assert [u["name"] for u in users[1:]] == ["agent0", "agent1"]
    for user in users:
        assert user["ssh_authorized_keys"] == [_TEST_SSH_PUBKEY]
    write_files = cast(list[dict[str, object]], doc["write_files"])
    assert len(write_files) == _AGENT_COUNT_2 * _EXPECTED_WRITE_FILES_PER_AGENT
    assert write_files[0]["path"] == "/home/agent0/config.json"
    assert write_files[1]["path"] == "/home/agent0/system_prompt.txt"
    runcmd = cast(list[str], doc["runcmd"])
    assert runcmd == ["loginctl enable-linger agent0", "loginctl enable-linger agent1"]
    assert doc["ssh_pwauth"] is False
    assert doc["disable_root"] is False


def test_rendered_yaml_parses_for_four_agents() -> None:
    """Same guard against the collapse pattern at the §3.A7 max N=4 default scale."""
    cfg = _match_config(n_agents=_AGENT_COUNT_4)
    cb, pb = _blobs(_AGENT_COUNT_4)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    parsed = cast(object, yaml.safe_load(out))
    assert isinstance(parsed, dict)
    doc = cast(dict[str, object], parsed)
    users = cast(list[dict[str, object]], doc["users"])
    assert [u["name"] for u in users] == ["root", "agent0", "agent1", "agent2", "agent3"]
    runcmd = cast(list[str], doc["runcmd"])
    assert len(runcmd) == _AGENT_COUNT_4


def test_rendered_yaml_with_network_full_appends_iptables_runcmd() -> None:
    """network_policy=full must add iptables -F line as a SEPARATE runcmd entry."""
    cfg = _match_config(n_agents=_AGENT_COUNT_2, network_policy="full")
    cb, pb = _blobs(_AGENT_COUNT_2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    parsed = cast(object, yaml.safe_load(out))
    assert isinstance(parsed, dict)
    doc = cast(dict[str, object], parsed)
    runcmd = cast(list[str], doc["runcmd"])
    assert runcmd[:2] == ["loginctl enable-linger agent0", "loginctl enable-linger agent1"]
    assert "iptables -F" in runcmd
    assert "iptables -P INPUT ACCEPT" in runcmd
