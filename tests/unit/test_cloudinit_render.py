import json

import pytest

from common.errors import ConfigError
from orchestrator.cloudinit import render_user_data
from orchestrator.match_config import MatchConfig

_HEARTBEAT = 120
_GRACE = 30
_MAX_DURATION = 1800
_ARCHIVE_GRACE = 60
_TEST_SSH_PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAItestkey arenabench-test"
_AGENT_COUNT_4 = 4
_AGENT_COUNT_2 = 2


def _match_config(*, n_agents: int, network_policy: str = "allowlist") -> MatchConfig:
    payload: dict[str, object] = {
        "match_id": "demo-test",
        "n_agents": n_agents,
        "heartbeat_interval_s": _HEARTBEAT,
        "grace_period_s": _GRACE,
        "max_duration_s": _MAX_DURATION,
        "archive_grace_s": _ARCHIVE_GRACE,
        "network_policy": network_policy,
        "cgroup_limits": None,
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


def test_happy_2_agents_renders_users_chmod_secrets() -> None:
    cfg = _match_config(n_agents=2)
    cb, pb = _blobs(2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    assert out.startswith("#cloud-config")
    assert "name: agent0" in out
    assert "name: agent1" in out
    assert "chmod 0700 /home/agent0" in out
    assert "chmod 0700 /home/agent1" in out
    assert "/home/agent0/.secrets" in out
    assert "/home/agent1/.secrets" in out
    assert _TEST_SSH_PUBKEY in out
    assert "ssh_pwauth: false" in out
    assert "name: root" in out


def test_happy_4_agents_all_blocks_present() -> None:
    cfg = _match_config(n_agents=_AGENT_COUNT_4)
    cb, pb = _blobs(_AGENT_COUNT_4)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    for i in range(_AGENT_COUNT_4):
        assert f"name: agent{i}" in out
        assert f"chmod 0700 /home/agent{i}" in out


def test_secrets_block_renders_export_lines() -> None:
    """Round-19 lock: per-agent /home/agentN/.secrets carries `export KEY=value`
    lines for the agent's api_key_env values. SshTransport sources this file
    instead of passing keys via argv (§3.B9 raw-key-only-in-env).
    """
    cfg = _match_config(n_agents=_AGENT_COUNT_2)
    cb, pb = _blobs(_AGENT_COUNT_2)
    env = {
        0: (("ANTHROPIC_API_KEY", "sk-ant-1"),),
        1: (("OPENAI_API_KEY", "sk-openai-1"),),
    }

    out = render_user_data(
        match_config=cfg,
        config_blobs=cb,
        prompt_blobs=pb,
        ssh_pubkey=_TEST_SSH_PUBKEY,
        agent_env_vars=env,
    )

    assert "export ANTHROPIC_API_KEY=sk-ant-1" in out
    assert "export OPENAI_API_KEY=sk-openai-1" in out


def test_network_policy_full_flushes_iptables() -> None:
    cfg = _match_config(n_agents=_AGENT_COUNT_2, network_policy="full")
    cb, pb = _blobs(_AGENT_COUNT_2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    assert "iptables -F" in out
    assert "iptables -P INPUT ACCEPT" in out


def test_network_policy_allowlist_no_flush() -> None:
    cfg = _match_config(n_agents=_AGENT_COUNT_2)
    cb, pb = _blobs(_AGENT_COUNT_2)

    out = render_user_data(
        match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
    )

    assert "iptables -F" not in out


def test_missing_prompt_blob_raises_config_error() -> None:
    cfg = _match_config(n_agents=_AGENT_COUNT_2)
    cb = {0: '{"slot":0}', 1: '{"slot":1}'}
    pb = {0: "prompt0"}

    with pytest.raises(ConfigError) as exc_info:
        render_user_data(
            match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
        )

    assert exc_info.value.field == "prompt_blobs[1]"


def test_missing_config_blob_raises_config_error() -> None:
    cfg = _match_config(n_agents=_AGENT_COUNT_2)
    cb = {0: '{"slot":0}'}
    pb = {0: "p0", 1: "p1"}

    with pytest.raises(ConfigError) as exc_info:
        render_user_data(
            match_config=cfg, config_blobs=cb, prompt_blobs=pb, ssh_pubkey=_TEST_SSH_PUBKEY
        )

    assert exc_info.value.field == "config_blobs[1]"
