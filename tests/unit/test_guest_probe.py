import getpass
import os
import socket
import sys
import threading
from datetime import UTC, datetime

import pytest

from common.protocol import (
    BootAck,
    BootAckResponse,
    Envelope,
    Frame,
    HarnessExit,
    Kill0,
    Kill0Response,
    ProcList,
    ProcListResponse,
    parse_envelope,
    serialize_envelope,
)
from vm.guest_probe import boot_info, handle_request, kill0_alive, proc_pids_for_user, serve

TS = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)
SRC = "orchestrator"
KILL0_REQ = "k1"
PROC_REQ = "p1"
BOOT_REQ = "b1"
PID_LIVE = os.getpid()
PID_BAD = -1
PID_MISSING = 99_999_999
THREAD_TIMEOUT_S = 2.0


def _env(data: Frame) -> Envelope:
    return Envelope(v=1, ts=TS, seq=1, src=SRC, kind=data.kind, data=data)


def _fake_proc_pids(user: str) -> list[int]:
    _ = user
    return [PID_LIVE]


def _fake_boot_info() -> tuple[str, float]:
    return ("kernel", 1.0)


def test_kill0_alive_pid_states() -> None:
    assert kill0_alive(PID_LIVE) is True
    assert kill0_alive(PID_BAD) is False
    assert kill0_alive(PID_MISSING) is False


@pytest.mark.skipif(sys.platform != "linux", reason="needs /proc")
def test_proc_pids_for_user_includes_self() -> None:
    pids = proc_pids_for_user(getpass.getuser())
    assert pids and PID_LIVE in pids


@pytest.mark.skipif(sys.platform != "linux", reason="needs /proc")
def test_boot_info_reads_kernel_and_uptime() -> None:
    kernel, uptime_s = boot_info()
    assert kernel and isinstance(uptime_s, float)


def test_handle_request_round_trips_probe_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vm.guest_probe.proc_pids_for_user", _fake_proc_pids)
    monkeypatch.setattr("vm.guest_probe.boot_info", _fake_boot_info)

    kill0 = handle_request(_env(Kill0(request_id=KILL0_REQ, pid=PID_LIVE)))
    proc = handle_request(_env(ProcList(request_id=PROC_REQ, user=getpass.getuser())))
    boot = handle_request(_env(BootAck(request_id=BOOT_REQ)))

    assert kill0 is not None and isinstance(kill0.data, Kill0Response)
    assert kill0.data.request_id == KILL0_REQ
    assert kill0.data.pid == PID_LIVE
    assert proc is not None and isinstance(proc.data, ProcListResponse)
    assert proc.data.request_id == PROC_REQ
    assert proc.data.user == getpass.getuser()
    assert boot is not None and isinstance(boot.data, BootAckResponse)
    assert boot.data.request_id == BOOT_REQ
    assert boot.data.cid == 0


def test_handle_request_unknown_kind_returns_none() -> None:
    assert handle_request(_env(HarnessExit(reason="clean", last_turn=0))) is None


def test_serve_loopback_over_unix_socketpair() -> None:
    server, client = socket.socketpair()
    t = threading.Thread(target=serve, args=(server,), daemon=True)
    t.start()

    wire = serialize_envelope(_env(Kill0(request_id=KILL0_REQ, pid=PID_LIVE))) + "\n"
    client.sendall(wire.encode())
    resp = parse_envelope(client.recv(4096).decode().strip())
    client.close()
    t.join(THREAD_TIMEOUT_S)

    assert not t.is_alive()
    assert resp.kind == "kill0_response"
    assert isinstance(resp.data, Kill0Response)
    assert resp.data.request_id == KILL0_REQ
    assert resp.data.pid == PID_LIVE
