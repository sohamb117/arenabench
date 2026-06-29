from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from common.errors import TransportError
from common.protocol import (
    Envelope,
    HeartbeatTick,
    PidAnnounce,
    serialize_envelope,
)
from orchestrator.match_config import AgentEntry
from orchestrator.ssh_server import (
    AGENT_PORT_BASE,
    PROBE_PORT,
    SshOrchestratorServer,
    build_transport_configs,
    port_to_slot,
    slot_to_port,
)

NOW = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)
SSH_HOST = "127.0.0.1"
SSH_PORT = 22222
EXPECTED_PROBE_PORT = 9999
EXPECTED_AGENT_BASE = 10000
UNKNOWN_PORT = 99999


def _agents(n: int) -> list[AgentEntry]:
    return [AgentEntry(slot=i, user=f"agent{i}", config=f"c{i}.json") for i in range(n)]


def test_port_slot_round_trip() -> None:
    for slot in range(16):
        assert port_to_slot(slot_to_port(slot)) == slot


def test_agent_port_assignment_matches_plan_b6() -> None:
    srv = SshOrchestratorServer(agents=_agents(4), ssh_host=SSH_HOST, ssh_port=SSH_PORT)
    assert srv.agent_ports == {0: 10000, 1: 10001, 2: 10002, 3: 10003}


def test_probe_port_is_9999_per_plan_b6() -> None:
    assert PROBE_PORT == EXPECTED_PROBE_PORT
    assert AGENT_PORT_BASE == EXPECTED_AGENT_BASE


def test_unknown_port_raises_transport_error() -> None:
    srv = SshOrchestratorServer(agents=_agents(2), ssh_host=SSH_HOST, ssh_port=SSH_PORT)
    env = Envelope(
        v=1,
        ts=NOW,
        seq=0,
        src="orchestrator",
        dst="agent0",
        kind="heartbeat_tick",
        data=HeartbeatTick(elapsed_s=1.0, turn_hint=1),
    )
    with pytest.raises(TransportError):
        srv.recv_frame(UNKNOWN_PORT, timeout_s=0.1)
    with pytest.raises(TransportError):
        srv.send_frame(UNKNOWN_PORT, env)


def test_serialize_envelope_works_for_pid_announce_round_trip() -> None:
    env = Envelope(
        v=1,
        ts=NOW,
        seq=0,
        src="agent0",
        kind="pid_announce",
        data=PidAnnounce(
            pid=1001, user="agent0", uid=1001, hostname="vm", parser="json", model="m"
        ),
    )
    wire = serialize_envelope(env)
    assert wire.startswith('{"v":1')
    assert '"src":"agent0"' in wire
    assert '"kind":"pid_announce"' in wire


def test_agent_env_vars_propagate_to_per_slot_ssh_configs() -> None:
    """Gap-5 lock: build_transport_configs forwards agent_env_vars to each SshConfig."""
    agents = _agents(2)
    env_map = {
        0: (("ANTHROPIC_API_KEY", "sk-a"),),
        1: (("OPENAI_API_KEY", "sk-o"),),
    }

    configs = build_transport_configs(
        agents=agents,
        ssh_host=SSH_HOST,
        ssh_port=SSH_PORT,
        agent_env_vars=env_map,
    )

    assert configs[slot_to_port(0)].env_vars == (("ANTHROPIC_API_KEY", "sk-a"),)
    assert configs[slot_to_port(1)].env_vars == (("OPENAI_API_KEY", "sk-o"),)
    assert configs[PROBE_PORT].env_vars is None


def test_probe_remote_command_uses_stdio_flag() -> None:
    """Gap-2 lock: probe transport must invoke `vm.guest_probe --stdio`.

    Round-6 follow-up: must use the in-guest 3.12 venv interpreter, not /usr/bin/python3.
    """
    configs = build_transport_configs(agents=_agents(1), ssh_host=SSH_HOST, ssh_port=SSH_PORT)

    assert configs[PROBE_PORT].remote_command == (
        "/opt/arenabench-venv/bin/python3",
        "-m",
        "vm.guest_probe",
        "--stdio",
    )


def test_key_path_propagates_to_all_transports() -> None:
    """Gap-4 lock: key_path injected once at the server level propagates to every transport."""
    key = Path("/tmp/test-ssh-key")
    configs = build_transport_configs(
        agents=_agents(2),
        ssh_host=SSH_HOST,
        ssh_port=SSH_PORT,
        key_path=key,
    )

    for cfg in configs.values():
        assert cfg.key_path == key


@pytest.mark.e2e
def test_real_ssh_round_trip_via_localhost() -> None:
    if not os.environ.get("ARENABENCH_E2E"):
        pytest.skip("set ARENABENCH_E2E=1 + ARENABENCH_SSH_TARGET to test against a real ssh host")
    pytest.skip("requires a reachable ssh host with the harness installed")
