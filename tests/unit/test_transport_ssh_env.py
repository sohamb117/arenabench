"""Tests for SshConfig.env_vars / API-key forwarding + the @e2e roundtrip.

Split from test_transport_ssh.py for the 250 LOC cap.
"""

import os
from datetime import UTC, datetime

import pytest

from common.protocol import Envelope, PidAnnounce, parse_envelope, serialize_envelope
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


def test_env_vars_prepend_env_prefix_to_command(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gap-5 lock: SshConfig.env_vars must inject `env K=V` BEFORE python3 on the remote."""
    process = FakeSshProcess()
    argv = patch_popen(monkeypatch, process)

    SshTransport(
        SshConfig(
            host="127.0.0.1",
            port=22222,
            user="agent0",
            env_vars=(("ANTHROPIC_API_KEY", "sk-test-1"), ("FOO", "bar")),
        )
    ).open()

    env_index = argv.index("env")
    py_index = argv.index("/opt/arenabench-venv/bin/python3")
    assert env_index < py_index
    assert argv[env_index + 1] == "ANTHROPIC_API_KEY=sk-test-1"
    assert argv[env_index + 2] == "FOO=bar"
    process.close_pipes()


def test_env_vars_none_means_no_env_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeSshProcess()
    argv = patch_popen(monkeypatch, process)

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
