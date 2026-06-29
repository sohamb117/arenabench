"""Orchestrator-side SSH transport multiplexer.

The R2-fallback equivalent of `vsock_server.VsockServer`: instead of accepting
inbound vsock connections from per-agent harnesses, the orchestrator opens
one SSH subprocess per agent that runs `python3 -m harness ...` inside the
guest VM. Each SSH stdin/stdout pair is one bidirectional JSONL channel.

Implements the `_lifecycle_state.VsockServerLike` Protocol so
`lifecycle.run_match` accepts it without modification.

Port convention (mirrors §3.B6 for symmetry): agent slot k → "port" 10000+k.
The guest-probe lives on "port" 9999 and is a separate SshTransport that
proxies to /usr/local/sbin/arenabench-guest-probe inside the VM.
"""

from __future__ import annotations

from pathlib import Path

from common.errors import TransportError
from common.protocol import Envelope
from harness.transport_ssh import SshConfig, SshTransport
from orchestrator.match_config import AgentEntry

PROBE_PORT = 9999
AGENT_PORT_BASE = 10000
_GUEST_PYTHON = "/opt/arenabench-venv/bin/python3"


def slot_to_port(slot: int) -> int:
    return AGENT_PORT_BASE + slot


def port_to_slot(port: int) -> int:
    return port - AGENT_PORT_BASE


def build_transport_configs(
    *,
    agents: list[AgentEntry],
    ssh_host: str,
    ssh_port: int,
    key_path: Path | None = None,
    probe_user: str = "root",
    connect_timeout_s: float = 30.0,
    agent_env_vars: dict[int, tuple[tuple[str, str], ...]] | None = None,
) -> dict[int, SshConfig]:
    """Build the per-port SshConfig dict the SshOrchestratorServer will multiplex.

    Extracted so tests can verify env_vars/key_path/remote_command propagation
    without reaching into the SshOrchestratorServer's internal transport map.
    """
    env_map = agent_env_vars or {}
    configs: dict[int, SshConfig] = {}
    for agent in agents:
        configs[slot_to_port(agent.slot)] = SshConfig(
            host=ssh_host,
            port=ssh_port,
            user=agent.user,
            key_path=key_path,
            connect_timeout_s=connect_timeout_s,
            env_vars=env_map.get(agent.slot),
        )
    configs[PROBE_PORT] = SshConfig(
        host=ssh_host,
        port=ssh_port,
        user=probe_user,
        key_path=key_path,
        connect_timeout_s=connect_timeout_s,
        remote_command=(_GUEST_PYTHON, "-m", "vm.guest_probe", "--stdio"),
    )
    return configs


class SshOrchestratorServer:
    """Per-match SSH multiplexer. One SshTransport per agent + one for guest-probe.

    Use as a context manager so subprocesses are cleaned up on exception.
    """

    def __init__(
        self,
        *,
        agents: list[AgentEntry],
        ssh_host: str,
        ssh_port: int,
        key_path: Path | None = None,
        probe_user: str = "root",
        connect_timeout_s: float = 30.0,
        agent_env_vars: dict[int, tuple[tuple[str, str], ...]] | None = None,
    ) -> None:
        configs = build_transport_configs(
            agents=agents,
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            key_path=key_path,
            probe_user=probe_user,
            connect_timeout_s=connect_timeout_s,
            agent_env_vars=agent_env_vars,
        )
        self._transports: dict[int, SshTransport] = {
            port: SshTransport(cfg) for port, cfg in configs.items()
        }

    def start(self) -> None:
        for t in self._transports.values():
            t.open()

    def stop(self) -> None:
        for t in self._transports.values():
            t.close()

    def recv_frame(self, port: int, timeout_s: float | None) -> Envelope | None:
        if port not in self._transports:
            raise TransportError(f"unknown port {port}")
        return self._transports[port].recv(timeout_s=timeout_s)

    def send_frame(self, port: int, env: Envelope) -> None:
        if port not in self._transports:
            raise TransportError(f"unknown port {port}")
        self._transports[port].send(env)

    def is_open(self, port: int) -> bool:
        transport = self._transports.get(port)
        if transport is None:
            return False
        return transport.is_open()

    @property
    def agent_ports(self) -> dict[int, int]:
        return {port_to_slot(p): p for p in self._transports if p != PROBE_PORT}

    def __enter__(self) -> SshOrchestratorServer:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
