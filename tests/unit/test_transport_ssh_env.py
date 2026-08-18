"""Tests for SSH-launched harness invocation + the @e2e roundtrip.

Split from test_transport_ssh.py for the 250 LOC cap. Round-19 reshape:
SSH no longer carries `env KEY=value` argv (raw-key leak via /proc); the
default remote command is now `bash -lc '. ~/.secrets; exec python3 -m
harness ~/config.json'` so the keys live in /home/agentN/.secrets (mode
0600, owner agentN, seeded by cloud-init).
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


def test_default_remote_command_sources_secrets_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-19/20 §3.B9 lock: API keys must NOT appear in ssh argv. SshTransport's
    default remote command sources /home/agentN/.secrets via bash -lc, with the
    body shlex-quoted so the remote shell parses it as a single -c argument
    (Oracle round-20 caught the un-quoted semicolon splitting into two commands).
    """
    process = FakeSshProcess()
    argv = patch_popen(monkeypatch, process)

    SshTransport(SshConfig(host="127.0.0.1", port=22222, user="agent0")).open()

    bash_idx = argv.index("bash")
    assert argv[bash_idx + 1] == "-lc"
    cmd = argv[bash_idx + 2]
    assert cmd.startswith("'") and cmd.endswith("'"), cmd
    body = cmd[1:-1]
    assert body == (
        ". /home/agent0/.secrets 2>/dev/null; "
        "export LITELLM_LOCAL_MODEL_COST_MAP=True; "
        "exec /opt/arenabench-venv/bin/python3 -m harness /home/agent0/config.json"
    )
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
