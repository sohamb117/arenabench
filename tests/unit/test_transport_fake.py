from datetime import UTC, datetime

import pytest

from common.errors import TransportError
from common.protocol import Envelope, PidAnnounce
from harness.transport_fake import InMemoryTransport

TS = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
SEQ_A = 1
SEQ_B = 2
SRC_A = "harness-a"
SRC_B = "harness-b"


def _env(seq: int, src: str, pid: int) -> Envelope:
    return Envelope(
        ts=TS,
        seq=seq,
        src=src,
        kind="pid_announce",
        data=PidAnnounce(
            pid=pid,
            user="user",
            uid=1000,
            hostname="host",
            parser="json",
            model="model",
        ),
    )


def test_peer_send_recv_close_fifo() -> None:
    a = InMemoryTransport()
    b = a.peer()
    env_a = _env(SEQ_A, SRC_A, 11)
    env_b = _env(SEQ_B, SRC_B, 22)

    assert a.is_open() and b.is_open()
    a.send(env_a)
    assert b.recv() == env_a
    b.send(env_b)
    assert a.recv() == env_b
    assert a.recv(timeout_s=0.05) is None

    a.close()

    assert not a.is_open() and not b.is_open()
    with pytest.raises(TransportError):
        a.send(env_a)
    assert b.recv() is None


def test_peer_returns_same_instance() -> None:
    a = InMemoryTransport()

    assert a.peer() is a.peer()


def test_fifo_preserves_order() -> None:
    a = InMemoryTransport()
    b = a.peer()
    env_1 = _env(3, SRC_A, 31)
    env_2 = _env(4, SRC_A, 32)
    env_3 = _env(5, SRC_A, 33)

    a.send(env_1)
    a.send(env_2)
    a.send(env_3)

    assert [b.recv(), b.recv(), b.recv()] == [env_1, env_2, env_3]
