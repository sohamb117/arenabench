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


def slot_to_port(slot: int) -> int:
    return AGENT_PORT_BASE + slot


def port_to_slot(port: int) -> int:
    return port - AGENT_PORT_BASE


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
    ) -> None:
        self._transports: dict[int, SshTransport] = {}
        for agent in agents:
            cfg = SshConfig(
                host=ssh_host,
                port=ssh_port,
                user=agent.user,
                key_path=key_path,
                connect_timeout_s=connect_timeout_s,
            )
            self._transports[slot_to_port(agent.slot)] = SshTransport(cfg)
        probe_cfg = SshConfig(
            host=ssh_host,
            port=ssh_port,
            user=probe_user,
            key_path=key_path,
            connect_timeout_s=connect_timeout_s,
        )
        self._transports[PROBE_PORT] = SshTransport(probe_cfg)

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

    @property
    def agent_ports(self) -> dict[int, int]:
        return {port_to_slot(p): p for p in self._transports if p != PROBE_PORT}

    def __enter__(self) -> SshOrchestratorServer:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
