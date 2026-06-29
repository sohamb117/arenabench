from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from common.errors import TransportError
from common.protocol import (
    MAX_FRAME_BYTES,
    Envelope,
    PidAnnounce,
    Shutdown,
    serialize_envelope,
)
from orchestrator.vsock_server import UnixSocketBackend, VsockServer

PROBE_PORT = 9999
FIRST_AGENT_PORT = 10000
AGENT_COUNT = 4
SOCKET_TIMEOUT_S = 1.0
SHORT_TIMEOUT_S = 0.2
FRAME_TOO_LARGE_PADDING = MAX_FRAME_BYTES + 1


def make_pid_announce(seq: int = 1) -> Envelope:
    return Envelope(
        v=1,
        ts=datetime(2026, 6, 28, 19, 0, 0, tzinfo=UTC),
        seq=seq,
        src="agent0",
        dst="orchestrator",
        kind="pid_announce",
        data=PidAnnounce(
            pid=1234,
            user="agent0",
            uid=1000,
            hostname="guest",
            parser="json",
            model="gpt",
        ),
    )


def make_shutdown(seq: int = 2) -> Envelope:
    return Envelope(
        v=1,
        ts=datetime(2026, 6, 28, 19, 0, 1, tzinfo=UTC),
        seq=seq,
        src="orchestrator",
        dst="agent0",
        kind="shutdown",
        data=Shutdown(reason="test"),
    )


def socket_path(base_dir: Path, port: int) -> Path:
    return base_dir / f"port-{port}.sock"


def connect_client(base_dir: Path, port: int) -> socket.socket:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(SOCKET_TIMEOUT_S)
    current_dir = Path.cwd()
    try:
        os.chdir(base_dir)
        client.connect(socket_path(base_dir, port).name)
    finally:
        os.chdir(current_dir)
    return client


@pytest.fixture
def server(tmp_path: Path) -> Iterator[VsockServer]:
    vsock_server = VsockServer(UnixSocketBackend(tmp_path), n_agents=1)
    vsock_server.start()
    try:
        yield vsock_server
    finally:
        vsock_server.stop()


def test_round_trip_pid_announce_when_client_sends_jsonl(tmp_path: Path) -> None:
    given = make_pid_announce()
    vsock_server = VsockServer(UnixSocketBackend(tmp_path), n_agents=1)
    vsock_server.start()

    try:
        with connect_client(tmp_path, FIRST_AGENT_PORT) as client:
            vsock_server.wait_for_connect(FIRST_AGENT_PORT, SOCKET_TIMEOUT_S)

            client.sendall(f"{serialize_envelope(given)}\n".encode())

            assert vsock_server.recv_frame(FIRST_AGENT_PORT, SOCKET_TIMEOUT_S) == given
    finally:
        vsock_server.stop()


def test_start_listens_on_agent_ports_and_probe_port(tmp_path: Path) -> None:
    vsock_server = VsockServer(UnixSocketBackend(tmp_path), n_agents=AGENT_COUNT)
    vsock_server.start()

    try:
        ports = [*(FIRST_AGENT_PORT + index for index in range(AGENT_COUNT)), PROBE_PORT]
        clients = [connect_client(tmp_path, port) for port in ports]
        try:
            for port in ports:
                assert vsock_server.wait_for_connect(port, SOCKET_TIMEOUT_S).remote_addr
        finally:
            for client in clients:
                client.close()
    finally:
        vsock_server.stop()


def test_send_frame_writes_serialized_jsonl_to_client(server: VsockServer, tmp_path: Path) -> None:
    given = make_shutdown()

    with connect_client(tmp_path, FIRST_AGENT_PORT) as client:
        server.wait_for_connect(FIRST_AGENT_PORT, SOCKET_TIMEOUT_S)

        server.send_frame(FIRST_AGENT_PORT, given)

        assert client.recv(MAX_FRAME_BYTES).decode() == f"{serialize_envelope(given)}\n"


def test_recv_frame_returns_none_on_disconnect(server: VsockServer, tmp_path: Path) -> None:
    client = connect_client(tmp_path, FIRST_AGENT_PORT)
    server.wait_for_connect(FIRST_AGENT_PORT, SOCKET_TIMEOUT_S)

    client.close()

    assert server.recv_frame(FIRST_AGENT_PORT, SOCKET_TIMEOUT_S) is None


def test_stop_closes_listeners_and_connections(server: VsockServer, tmp_path: Path) -> None:
    client = connect_client(tmp_path, FIRST_AGENT_PORT)
    server.wait_for_connect(FIRST_AGENT_PORT, SOCKET_TIMEOUT_S)

    server.stop()

    assert server.recv_frame(FIRST_AGENT_PORT, SHORT_TIMEOUT_S) is None
    with pytest.raises(TransportError):
        server.send_frame(FIRST_AGENT_PORT, make_shutdown())
    with pytest.raises(OSError):
        connect_client(tmp_path, PROBE_PORT)
    client.close()


def test_malformed_json_raises_transport_error(server: VsockServer, tmp_path: Path) -> None:
    with connect_client(tmp_path, FIRST_AGENT_PORT) as client:
        server.wait_for_connect(FIRST_AGENT_PORT, SOCKET_TIMEOUT_S)

        client.sendall(b"{bad json}\n")

        with pytest.raises(TransportError):
            server.recv_frame(FIRST_AGENT_PORT, SOCKET_TIMEOUT_S)


def test_oversized_frame_raises_transport_error(server: VsockServer, tmp_path: Path) -> None:
    with connect_client(tmp_path, FIRST_AGENT_PORT) as client:
        server.wait_for_connect(FIRST_AGENT_PORT, SOCKET_TIMEOUT_S)

        with ThreadPoolExecutor(max_workers=1) as executor:
            received = executor.submit(server.recv_frame, FIRST_AGENT_PORT, SOCKET_TIMEOUT_S)
            client.sendall(("x" * FRAME_TOO_LARGE_PADDING + "\n").encode())

            with pytest.raises(TransportError):
                received.result(timeout=SOCKET_TIMEOUT_S)
