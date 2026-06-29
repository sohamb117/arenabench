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
    ensure_ssh_keypair,
    load_agent_blobs,
    resolve_agent_env_vars,
    wait_for_ssh_ready,
)
from orchestrator.match_config import AgentEntry

ED25519_PUBKEY_PREFIX = "ssh-ed25519 "
EXPECTED_TEST_ARGS_LEN = 2


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


def _readiness_agents(n: int) -> list[AgentEntry]:
    return [AgentEntry(slot=i, user=f"agent{i}", config=f"c{i}.json") for i in range(n)]


def test_wait_for_ssh_ready_returns_when_probe_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round-12 lock: must actually exec ssh + run readiness command, not just TCP-connect."""
    captured_argv: list[str] = []

    def fake_run(
        argv: list[str], *, capture_output: bool, text: bool, timeout: float, check: bool
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output
        _ = text
        _ = timeout
        _ = check
        captured_argv.extend(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("orchestrator._cli_helpers.subprocess.run", fake_run)

    wait_for_ssh_ready(
        host="127.0.0.1",
        port=22222,
        key_path=tmp_path / "ssh_key",
        agents=_readiness_agents(2),
        deadline_s=5.0,
    )

    joined = " ".join(captured_argv)
    assert captured_argv[0] == "ssh"
    assert "-i" in captured_argv
    assert "BatchMode=yes" in captured_argv
    assert "root@127.0.0.1" in captured_argv
    assert "cloud-init status --wait" in joined
    assert "test -x /opt/arenabench-venv/bin/python3" in joined
    assert "test -f /home/agent0/config.json" in joined
    assert "test -f /home/agent1/system_prompt.txt" in joined


def test_wait_for_ssh_ready_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Loop until probe succeeds; first probe fails, second succeeds."""
    calls = {"n": 0}

    def fake_run(
        argv: list[str], *, capture_output: bool, text: bool, timeout: float, check: bool
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output
        _ = text
        _ = timeout
        _ = check
        calls["n"] += 1
        rc = 0 if calls["n"] >= EXPECTED_TEST_ARGS_LEN else 255
        return subprocess.CompletedProcess(args=argv, returncode=rc, stdout="", stderr="boom")

    monkeypatch.setattr("orchestrator._cli_helpers.subprocess.run", fake_run)

    def fake_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr("orchestrator._cli_helpers.time.sleep", fake_sleep)

    wait_for_ssh_ready(
        host="127.0.0.1",
        port=22222,
        key_path=tmp_path / "ssh_key",
        agents=_readiness_agents(1),
        deadline_s=10.0,
    )

    assert calls["n"] == EXPECTED_TEST_ARGS_LEN


def test_wait_for_ssh_ready_raises_timeout_when_probe_keeps_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Never succeed → TimeoutError with last stderr in message."""

    def fake_run(
        argv: list[str], *, capture_output: bool, text: bool, timeout: float, check: bool
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output
        _ = text
        _ = timeout
        _ = check
        return subprocess.CompletedProcess(
            args=argv, returncode=255, stdout="", stderr="Permission denied (publickey)"
        )

    monkeypatch.setattr("orchestrator._cli_helpers.subprocess.run", fake_run)

    def fake_sleep2(_s: float) -> None:
        return None

    monkeypatch.setattr("orchestrator._cli_helpers.time.sleep", fake_sleep2)

    with pytest.raises(TimeoutError, match="Permission denied"):
        wait_for_ssh_ready(
            host="127.0.0.1",
            port=22222,
            key_path=tmp_path / "ssh_key",
            agents=_readiness_agents(1),
            deadline_s=0.01,
        )
