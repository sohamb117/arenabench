"""Unit tests for orchestrator._cli_helpers.

Locks the Oracle-round-3 gap fixes:
- load_agent_blobs rewrites system_prompt_path → 'system_prompt.txt' (gap #3)
- ensure_ssh_keypair generates an ed25519 keypair (gap #4)
- resolve_agent_env_vars surfaces missing host env vars (gap #5)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from common.errors import ConfigError
from orchestrator._cli_helpers import (
    build_egress_proxy,
    ensure_ssh_keypair,
    load_agent_blobs,
    resolve_agent_env_vars,
)
from orchestrator.cloudinit import GuestProxyTarget
from orchestrator.match_config import AgentEntry, MatchConfig

ED25519_PUBKEY_PREFIX = "ssh-ed25519 "
EXPECTED_TEST_ARGS_LEN = 2
_GUEST_ADDR = "10.0.2.100"


def _match_cfg(network_policy: str, extra: list[str] | None = None) -> MatchConfig:
    return MatchConfig.model_validate(
        {
            "match_id": "demo-1v1",
            "n_agents": 2,
            "heartbeat_interval_s": 120,
            "grace_period_s": 30,
            "max_duration_s": 1800,
            "archive_grace_s": 60,
            "network_policy": network_policy,
            "cgroup_limits": None,
            "domain_allowlist_extra": extra,
            "agents": [
                {"slot": 0, "user": "agent0", "config": "c0.json"},
                {"slot": 1, "user": "agent1", "config": "c1.json"},
            ],
        }
    )


def test_build_egress_proxy_allowlist_starts_proxy(tmp_path: Path) -> None:
    proxy, target = build_egress_proxy(_match_cfg("allowlist", ["api.custom.ai"]), tmp_path)
    try:
        assert proxy is not None
        assert proxy.port > 0
        assert target == GuestProxyTarget(guest_addr=_GUEST_ADDR, port=proxy.port)
    finally:
        if proxy is not None:
            proxy.stop()


def test_build_egress_proxy_full_returns_none(tmp_path: Path) -> None:
    proxy, target = build_egress_proxy(_match_cfg("full"), tmp_path)
    assert proxy is None
    assert target is None


def _write_agent(tmp_path: Path, slot: int, *, system_prompt_path: str) -> Path:
    agent_dir = tmp_path / "configs" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = tmp_path / system_prompt_path
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(f"prompt-for-slot-{slot}\n", encoding="utf-8")
    cfg_path = agent_dir / f"a{slot}.json"
    cfg_path.write_text(
        json.dumps(
            {
                "model": "test-model",
                "temperature": 0.7,
                "max_tokens": 1000,
                "request_timeout_s": 60,
                "num_retries": 1,
                "parser": "json",
                "api_key_env": "ANTHROPIC_API_KEY",
                "system_prompt_path": system_prompt_path,
            }
        ),
        encoding="utf-8",
    )
    return cfg_path


def test_load_agent_blobs_rewrites_system_prompt_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Gap-3 lock: harness on the guest must find system_prompt.txt next to config.json."""
    monkeypatch.setattr("orchestrator._cli_helpers._REPO_ROOT", tmp_path)
    _write_agent(tmp_path, 0, system_prompt_path="configs/prompts/adversarial.txt")
    agents = [AgentEntry(slot=0, user="agent0", config="configs/agents/a0.json")]

    config_blobs, prompt_blobs = load_agent_blobs(agents)

    rewritten = cast(dict[str, object], json.loads(config_blobs[0]))
    assert rewritten["system_prompt_path"] == "system_prompt.txt"
    assert prompt_blobs[0] == "prompt-for-slot-0\n"


def test_load_agent_blobs_raises_when_config_not_object(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("orchestrator._cli_helpers._REPO_ROOT", tmp_path)
    (tmp_path / "configs" / "agents").mkdir(parents=True)
    (tmp_path / "configs" / "agents" / "bad.json").write_text("[1, 2, 3]", encoding="utf-8")
    agents = [AgentEntry(slot=0, user="agent0", config="configs/agents/bad.json")]

    with pytest.raises(ConfigError) as exc:
        load_agent_blobs(agents)

    assert exc.value.field == "<root>"


def test_resolve_agent_env_vars_surfaces_host_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Gap-5 lock: api_key_env must be read from host env and packaged per slot."""
    monkeypatch.setattr("orchestrator._cli_helpers._REPO_ROOT", tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-12345")
    _write_agent(tmp_path, 0, system_prompt_path="configs/prompts/p.txt")
    agents = [AgentEntry(slot=0, user="agent0", config="configs/agents/a0.json")]

    out = resolve_agent_env_vars(agents)

    assert out == {0: (("ANTHROPIC_API_KEY", "sk-ant-test-12345"),)}


def test_resolve_agent_env_vars_raises_when_var_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("orchestrator._cli_helpers._REPO_ROOT", tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    _write_agent(tmp_path, 0, system_prompt_path="configs/prompts/p.txt")
    agents = [AgentEntry(slot=0, user="agent0", config="configs/agents/a0.json")]

    with pytest.raises(ConfigError) as exc:
        resolve_agent_env_vars(agents)

    assert exc.value.field == "ANTHROPIC_API_KEY"


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not installed")
def test_ensure_ssh_keypair_generates_ed25519(tmp_path: Path) -> None:
    """Gap-4 lock: per-match keypair lands in overlay_dir; pubkey is ed25519."""
    private_path, pubkey = ensure_ssh_keypair(tmp_path)

    assert private_path.is_file()
    assert pubkey.startswith(ED25519_PUBKEY_PREFIX)
    assert (tmp_path / "ssh_key.pub").read_text(encoding="utf-8").strip() == pubkey
    octal_perms = oct(private_path.stat().st_mode & 0o777)
    assert octal_perms == "0o600"


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen not installed")
def test_ensure_ssh_keypair_is_idempotent(tmp_path: Path) -> None:
    p1, k1 = ensure_ssh_keypair(tmp_path)
    p2, k2 = ensure_ssh_keypair(tmp_path)

    assert p1 == p2
    assert k1 == k2


def test_ensure_ssh_keypair_raises_when_ssh_keygen_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fake_run(argv: list[str], *, check: bool) -> subprocess.CompletedProcess[bytes]:
        _ = argv
        _ = check
        raise subprocess.CalledProcessError(returncode=1, cmd="ssh-keygen")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        ensure_ssh_keypair(tmp_path)
