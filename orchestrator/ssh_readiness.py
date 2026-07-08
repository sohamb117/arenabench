from __future__ import annotations

import subprocess
import time
from pathlib import Path

from orchestrator.match_config import AgentEntry

_SSH_READY_POLL_INTERVAL_S = 2.0
_SSH_READY_PROBE_TIMEOUT_S = 60.0


def wait_for_ssh_ready(
    *,
    host: str,
    port: int,
    key_path: Path,
    agents: list[AgentEntry],
    deadline_s: float,
) -> None:
    """Wait for VM SSH key-auth + cloud-init + per-agent files to be ready.

    A raw TCP-connect to the SSH port is not enough — sshd listens long
    before cloud-init has run write_files / created users / installed keys.
    Driving SshOrchestratorServer.start() before cloud-init finishes
    races into PROVISIONING timeout because /home/agentN/config.json does
    not yet exist. This function actually logs in (key auth) and runs:
      cloud-init status --wait && test -x /opt/arenabench-venv/bin/python3
      && (per-agent) test -f /home/agentN/config.json
      && (per-agent) test -f /home/agentN/system_prompt.txt
    Raises TimeoutError if not ready within `deadline_s`.
    """
    deadline = time.monotonic() + deadline_s
    last_stderr = ""
    while time.monotonic() < deadline:
        try:
            result = _ssh_ready_probe(host=host, port=port, key_path=key_path, agents=agents)
        except subprocess.TimeoutExpired as exc:
            last_stderr = (
                f"ssh probe exceeded {_SSH_READY_PROBE_TIMEOUT_S}s "
                f"(cloud-init likely still running): {exc}"
            )
            time.sleep(_SSH_READY_POLL_INTERVAL_S)
            continue
        if result.returncode == 0:
            return
        last_stderr = result.stderr or result.stdout
        time.sleep(_SSH_READY_POLL_INTERVAL_S)
    raise TimeoutError(f"VM ssh-ready check exceeded {deadline_s}s; last={last_stderr[-200:]}")


def _ssh_ready_probe(
    *, host: str, port: int, key_path: Path, agents: list[AgentEntry]
) -> subprocess.CompletedProcess[str]:
    parts: list[str] = [
        "cloud-init status --wait",
        "test -x /opt/arenabench-venv/bin/python3",
    ]
    for agent in agents:
        parts.append(f"test -f /home/{agent.user}/config.json")
        parts.append(f"test -f /home/{agent.user}/system_prompt.txt")
    return subprocess.run(
        [
            "ssh",
            "-i",
            str(key_path),
            "-p",
            str(port),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            f"root@{host}",
            " && ".join(parts),
        ],
        capture_output=True,
        text=True,
        timeout=_SSH_READY_PROBE_TIMEOUT_S,
        check=False,
    )
