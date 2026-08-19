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
PRIVATE_FILE_MODE = 0o600


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
        "bash",
        "-lc",
        "'. /home/agent0/.secrets 2>/dev/null; "
        "export LITELLM_LOCAL_MODEL_COST_MAP=True; "
        "exec /opt/arenabench-venv/bin/python3 -m harness /home/agent0/config.json'",
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


def test_recv_drains_buffered_envelope_after_ssh_process_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    env = _env()
    process.write_stdout(f"{serialize_envelope(env)}\n".encode())
    process.returncode = 1

    assert transport.recv(timeout_s=0.05) == env
    process.close_pipes()


def test_recv_skips_blank_lines_before_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    env = _env()
    process.write_stdout(f"\n  \n{serialize_envelope(env)}\n".encode())

    assert transport.recv(timeout_s=0.05) == env
    process.close_pipes()


def test_recv_skips_non_protocol_stdout_before_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    env = _env()
    process.write_stdout(f"Give Feedback / Get Help\n{serialize_envelope(env)}\n".encode())

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


def test_open_raises_when_ssh_exits_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-16 lock: SshTransport.open() must detect fast-fail (auth denied,
    bad host) by polling for early exit. Without this, BatchMode=yes auth
    failures returned a "successful" open() and lifecycle would hit a
    misleading provisioning_timeout instead of an immediate transport error.
    """
    process = FakeSshProcess()
    process.returncode = 255
    _ = patch_popen(monkeypatch, process)

    with pytest.raises(TransportError, match="ssh exited immediately with rc=255"):
        SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0")).open()


def test_stderr_log_is_private_and_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = FakeSshProcess()
    _ = patch_popen(monkeypatch, process)
    path = tmp_path / "agents" / "00" / "stderr.log"
    transport = SshTransport(
        SshConfig(host="127.0.0.1", port=22222, user="agent0", stderr_path=path)
    )

    transport.open()
    assert path.stat().st_mode & 0o777 == PRIVATE_FILE_MODE
    transport.close()

    assert transport.is_open() is False


_ = os
