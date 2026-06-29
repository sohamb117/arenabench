import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from common.errors import TransportError
from common.protocol import Envelope, PidAnnounce, parse_envelope, serialize_envelope
from harness.transport import Transport
from harness.transport_ssh import SshConfig, SshTransport

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


class RecordingStdin:
    def __init__(self) -> None:
        self.data = bytearray()
        self.flush_count = 0
        self.closed = False

    def write(self, data: bytes) -> int:
        self.data.extend(data)
        return len(data)

    def flush(self) -> None:
        self.flush_count += 1

    def close(self) -> None:
        self.closed = True


class FakeSshProcess:
    def __init__(self) -> None:
        read_fd, write_fd = os.pipe()
        self.stdin = RecordingStdin()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self._write_fd = write_fd
        self.terminated = False
        self.killed = False
        self.returncode: int | None = None

    def write_stdout(self, payload: bytes) -> None:
        os.write(self._write_fd, payload)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def close_pipes(self) -> None:
        self.stdout.close()
        os.close(self._write_fd)


def _patch_popen(
    monkeypatch: pytest.MonkeyPatch,
    process: FakeSshProcess,
) -> list[str]:
    captured: list[str] = []

    def fake_popen(argv: list[str], **kwargs: bool | int | None) -> FakeSshProcess:
        captured.extend(argv)
        assert kwargs["stdin"] is not None
        assert kwargs["stdout"] is not None
        assert kwargs["text"] is False
        assert kwargs["bufsize"] == 0
        return process

    monkeypatch.setattr("harness.transport_ssh.subprocess.Popen", fake_popen)
    return captured


def test_open_constructs_expected_ssh_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    argv = _patch_popen(monkeypatch, process)

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
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "agent0@127.0.0.1",
        "python3",
        "-m",
        "harness",
        "/home/agent0/config.json",
    ]
    process.close_pipes()


def test_send_writes_serialized_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    env = _env()

    transport.send(env)

    assert process.stdin.data == f"{serialize_envelope(env)}\n".encode()
    assert process.stdin.flush_count == 1
    process.close_pipes()


def test_recv_returns_parsed_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    env = _env()
    process.write_stdout(f"{serialize_envelope(env)}\n".encode())

    assert transport.recv(timeout_s=0.05) == env
    process.close_pipes()


def test_recv_timeout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()

    assert transport.recv(timeout_s=0.05) is None
    process.close_pipes()


def test_recv_after_close_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    transport.close()

    assert transport.recv(timeout_s=0) is None


def test_send_after_close_raises_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()
    transport.close()

    with pytest.raises(TransportError):
        transport.send(_env())


def test_close_terminates_process_and_marks_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _patch_popen(monkeypatch, process)
    transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    transport.open()

    transport.close()

    assert process.terminated is True
    assert transport.is_open() is False


def test_context_manager_closes_on_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    _patch_popen(monkeypatch, process)

    with SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0")) as transport:
        assert transport.is_open() is True

    assert process.terminated is True
    assert transport.is_open() is False


def test_ssh_transport_satisfies_transport_protocol() -> None:
    transport: Transport = SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0"))
    assert transport.is_open() is False


def test_env_vars_prepend_env_prefix_to_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gap-5 lock: SshConfig.env_vars must inject `env K=V` BEFORE python3 on the remote."""
    process = FakeSshProcess()
    argv = _patch_popen(monkeypatch, process)

    SshTransport(
        SshConfig(
            host="127.0.0.1",
            port=22222,
            user="agent0",
            env_vars=(("ANTHROPIC_API_KEY", "sk-test-1"), ("FOO", "bar")),
        )
    ).open()

    env_index = argv.index("env")
    py_index = argv.index("python3")
    assert env_index < py_index
    assert argv[env_index + 1] == "ANTHROPIC_API_KEY=sk-test-1"
    assert argv[env_index + 2] == "FOO=bar"
    process.close_pipes()


def test_env_vars_none_means_no_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    argv = _patch_popen(monkeypatch, process)

    SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0")).open()

    assert "env" not in argv
    process.close_pipes()


@pytest.mark.e2e
def test_round_trip_real_ssh() -> None:
    if os.environ.get("ARENABENCH_E2E") != "1" or not os.environ.get("ARENABENCH_SSH_TARGET"):
        pytest.skip("set ARENABENCH_E2E=1 and ARENABENCH_SSH_TARGET=user@host:port")
    target = os.environ["ARENABENCH_SSH_TARGET"]
    user_host, port = target.rsplit(":", 1)
    user, host = user_host.split("@", 1)
    with SshTransport(SshConfig(host=host, port=int(port), user=user)) as transport:
        env = _env()
        transport.send(env)
        received = transport.recv(timeout_s=5.0)
    assert received is not None
    assert parse_envelope(serialize_envelope(received)) == received
