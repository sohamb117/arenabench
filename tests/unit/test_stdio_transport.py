"""Unit tests for harness.__main__.StdioTransport.

Locks the Oracle-round-3 gap fix: recv(timeout_s=...) must honor the
deadline via selector+os.read, not block on stdin.readline().
"""

from __future__ import annotations

import io
import os
import sys
import time
from datetime import UTC, datetime

import pytest

from common.errors import TransportError
from common.protocol import Envelope, PidAnnounce, serialize_envelope
from harness.__main__ import StdioTransport

TS = datetime(2026, 6, 29, 0, 0, tzinfo=UTC)
SHORT_TIMEOUT_S = 0.05
SHORT_TIMEOUT_MARGIN_S = 0.5


def _env() -> Envelope:
    return Envelope(
        v=1,
        ts=TS,
        seq=1,
        src="agent0",
        kind="pid_announce",
        data=PidAnnounce(
            pid=1234, user="agent0", uid=1000, hostname="vm", parser="json", model="m"
        ),
    )


def test_recv_returns_none_within_timeout_when_stdin_silent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gap-1 lock: recv must NOT block past timeout_s on a silent stdin."""
    r, w = os.pipe()
    try:
        fake_stdin = os.fdopen(r, "r")
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        transport = StdioTransport()
        start = time.monotonic()

        result = transport.recv(timeout_s=SHORT_TIMEOUT_S)

        elapsed = time.monotonic() - start
        assert result is None
        assert elapsed < SHORT_TIMEOUT_MARGIN_S
        transport.close()
    finally:
        os.close(w)


def test_recv_returns_envelope_when_line_arrives(monkeypatch: pytest.MonkeyPatch) -> None:
    r, w = os.pipe()
    try:
        fake_stdin = os.fdopen(r, "r")
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        transport = StdioTransport()
        env = _env()
        os.write(w, f"{serialize_envelope(env)}\n".encode())

        received = transport.recv(timeout_s=1.0)

        assert received == env
        transport.close()
    finally:
        os.close(w)


def test_recv_returns_none_when_stdin_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    r, w = os.pipe()
    fake_stdin = os.fdopen(r, "r")
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    transport = StdioTransport()
    os.close(w)

    result = transport.recv(timeout_s=1.0)

    assert result is None
    assert transport.is_open() is False
    transport.close()


def test_send_raises_after_close(monkeypatch: pytest.MonkeyPatch) -> None:
    r, w = os.pipe()
    try:
        fake_stdin = os.fdopen(r, "r")
        monkeypatch.setattr(sys, "stdin", fake_stdin)
        transport = StdioTransport()
        transport.close()

        with pytest.raises(TransportError):
            transport.send(_env())
    finally:
        os.close(w)


def test_send_writes_serialized_envelope_with_newline(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = io.BytesIO()
    fake_stdout = type("S", (), {"buffer": captured})()
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    r, _ = os.pipe()
    monkeypatch.setattr(sys, "stdin", os.fdopen(r, "r"))
    transport = StdioTransport()
    env = _env()

    transport.send(env)

    assert captured.getvalue() == f"{serialize_envelope(env)}\n".encode()
    transport.close()
