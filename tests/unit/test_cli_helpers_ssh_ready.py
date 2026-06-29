"""Unit tests for orchestrator._cli_helpers.wait_for_ssh_ready — split out of
test_cli_helpers.py for the 250 LOC cap. Locks the Oracle-round-12 gap fix:
proving SSH readiness requires real key-auth + cloud-init + per-agent file
checks, not just a TCP-connect probe.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator._cli_helpers import wait_for_ssh_ready
from orchestrator.match_config import AgentEntry

EXPECTED_RETRY_CALLS = 2


def _agents(n: int) -> list[AgentEntry]:
    return [AgentEntry(slot=i, user=f"agent{i}", config=f"c{i}.json") for i in range(n)]


def _noop_sleep(_s: float) -> None:
    return None


def test_wait_for_ssh_ready_returns_when_probe_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round-12 lock: probe runs real ssh argv (key auth + cloud-init check)."""
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
        agents=_agents(2),
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
    calls = {"n": 0}

    def fake_run(
        argv: list[str], *, capture_output: bool, text: bool, timeout: float, check: bool
    ) -> subprocess.CompletedProcess[str]:
        _ = capture_output
        _ = text
        _ = timeout
        _ = check
        calls["n"] += 1
        rc = 0 if calls["n"] >= EXPECTED_RETRY_CALLS else 255
        return subprocess.CompletedProcess(args=argv, returncode=rc, stdout="", stderr="boom")

    monkeypatch.setattr("orchestrator._cli_helpers.subprocess.run", fake_run)
    monkeypatch.setattr("orchestrator._cli_helpers.time.sleep", _noop_sleep)

    wait_for_ssh_ready(
        host="127.0.0.1",
        port=22222,
        key_path=tmp_path / "ssh_key",
        agents=_agents(1),
        deadline_s=10.0,
    )

    assert calls["n"] == EXPECTED_RETRY_CALLS


def test_wait_for_ssh_ready_raises_timeout_when_probe_keeps_failing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr("orchestrator._cli_helpers.time.sleep", _noop_sleep)

    with pytest.raises(TimeoutError, match="Permission denied"):
        wait_for_ssh_ready(
            host="127.0.0.1",
            port=22222,
            key_path=tmp_path / "ssh_key",
            agents=_agents(1),
            deadline_s=0.01,
        )
