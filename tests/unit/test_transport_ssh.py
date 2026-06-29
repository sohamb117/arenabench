import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from common.errors import TransportError
from common.protocol import Envelope, PidAnnounce, serialize_envelope
from harness.transport import Transport
from harness.transport_ssh import SshConfig, SshTransport
from tests.unit._ssh_transport_fakes import FakeSshProcess, patch_popen

TS = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)


def _env(seq: int = 1) -> Envelope:
    return Envelope(
        ts=TS,
        seq=seq,
        src="agent0",
        kind="pid_announce",
        data=PidAnnounce(
            pid=123,
            user="agent0",
            uid=1000,
            hostname="guest",
            parser="json",
            model="model",
        ),
    )


def test_open_constructs_expected_ssh_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    argv = patch_popen(monkeypatch, process)

    SshTransport(
        SshConfig(
            host="127.0.0.1",
            port=22222,
            user="agent0",
            key_path=Path("/tmp/key"),
            connect_timeout_s=7.9,
            keepalive_interval_s=12.2,
        )
    ).open()

    assert argv == [
        "ssh",
        "-p",
        "22222",
        "-i",
        "/tmp/key",
        "-o",
        "ConnectTimeout=7",
        "-o",
        "ServerAliveInterval=12",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "agent0@127.0.0.1",
        "/opt/arenabench-venv/bin/python3",
        "-m",
        "harness",
        "/home/agent0/config.json",
    ]
    process.close_pipes()


def test_send_writes_serialized_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    env = _env()

    transport.send(env)

    assert process.stdin.data == f"{serialize_envelope(env)}\n".encode()
    assert process.stdin.flush_count == 1
    process.close_pipes()


def test_recv_returns_parsed_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    env = _env()
    process.write_stdout(f"{serialize_envelope(env)}\n".encode())

    assert transport.recv(timeout_s=0.05) == env
    process.close_pipes()


def test_recv_timeout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()

    assert transport.recv(timeout_s=0.05) is None
    process.close_pipes()


def test_recv_after_close_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    transport.close()

    assert transport.recv(timeout_s=0) is None


def test_send_after_close_raises_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    transport.close()

    with pytest.raises(TransportError):
        transport.send(_env())


def test_close_terminates_process_and_marks_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()

    transport.close()

    assert process.terminated is True
    assert transport.is_open() is False


def test_context_manager_closes_on_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)

    with SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0")) as transport:
        assert transport.is_open() is True

    assert process.terminated is True
    assert transport.is_open() is False


def test_ssh_transport_satisfies_transport_protocol() -> None:
    transport: Transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    assert transport.is_open() is False


_ = os
