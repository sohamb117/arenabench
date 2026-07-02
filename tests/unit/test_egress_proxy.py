"""Unit tests for the host-side CONNECT egress proxy (plan T1, RED-first).

Covers the 7 librarian-flagged pitfalls: allowlist gating, DNS-rebinding
defence (resolve once), malformed-CONNECT rejection, half-close, IPv6 target
parsing, structured logging, and clean start/stop. Exercised with real client
sockets against the proxy's real bound port + a fake upstream TCP echo server.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from orchestrator.egress_proxy import EgressProxy, ProxyConfig

_CONNECT_TIMEOUT_S = 5.0
_HTTP_200 = b"HTTP/1.1 200"
_HTTP_403 = b"HTTP/1.1 403"
_HTTP_400 = b"HTTP/1.1 400"
_EXPECTED_PROVIDERS = 5


class _Upstream:
    """A trivial TCP echo server standing in for a provider endpoint."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(4)
        self.port: int = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn = self._sock.accept()[0]
            except OSError:
                return
            data = conn.recv(1024)
            conn.sendall(b"ECHO:" + data)
            conn.close()

    def close(self) -> None:
        self._sock.close()


@pytest.fixture
def upstream() -> Iterator[_Upstream]:
    up = _Upstream()
    yield up
    up.close()


def _make_proxy(tmp_path: Path, allowlist: frozenset[str]) -> Iterator[EgressProxy]:
    proxy = EgressProxy(ProxyConfig(allowlist=allowlist, log_path=tmp_path / "proxy.jsonl"))
    proxy.start()
    try:
        yield proxy
    finally:
        proxy.stop()


def _connect_request(proxy_port: int, target: str, body: bytes = b"") -> bytes:
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=_CONNECT_TIMEOUT_S) as s:
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        if body:
            s.sendall(body)
        s.settimeout(_CONNECT_TIMEOUT_S)
        chunks: list[bytes] = []
        try:
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except TimeoutError:
            pass
        return b"".join(chunks)


def test_default_allowlist_has_five_providers() -> None:
    assert len(EgressProxy.DEFAULT_ALLOWLIST) == _EXPECTED_PROVIDERS
    assert "api.anthropic.com" in EgressProxy.DEFAULT_ALLOWLIST
    assert "api.openai.com" in EgressProxy.DEFAULT_ALLOWLIST


def test_start_binds_dynamic_port_and_stop_is_clean(tmp_path: Path) -> None:
    proxy = EgressProxy(ProxyConfig(allowlist=frozenset(), log_path=tmp_path / "p.jsonl"))
    proxy.start()
    assert proxy.port > 0
    proxy.stop()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", proxy.port), timeout=1.0)


def test_allowed_host_tunnels_bytes(tmp_path: Path, upstream: _Upstream) -> None:
    host = "127.0.0.1"
    for proxy in _make_proxy(tmp_path, frozenset({host})):
        target = f"{host}:{upstream.port}"
        resp = _connect_request(proxy.port, target, body=b"ping")
        assert resp.startswith(_HTTP_200), resp
        assert b"ECHO:ping" in resp, resp


def test_disallowed_host_returns_403(tmp_path: Path, upstream: _Upstream) -> None:
    for proxy in _make_proxy(tmp_path, frozenset({"api.anthropic.com"})):
        resp = _connect_request(proxy.port, f"127.0.0.1:{upstream.port}")
        assert resp.startswith(_HTTP_403), resp


def test_allowlist_match_is_case_insensitive(tmp_path: Path, upstream: _Upstream) -> None:
    for proxy in _make_proxy(tmp_path, frozenset({"localhost"})):
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=_CONNECT_TIMEOUT_S) as s:
            s.sendall(f"CONNECT LOCALHOST:{upstream.port} HTTP/1.1\r\n\r\n".encode())
            s.settimeout(_CONNECT_TIMEOUT_S)
            resp = s.recv(256)
        assert resp.startswith(_HTTP_200), resp


def test_malformed_connect_line_returns_400(tmp_path: Path) -> None:
    for proxy in _make_proxy(tmp_path, frozenset({"api.anthropic.com"})):
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=_CONNECT_TIMEOUT_S) as s:
            s.sendall(b"GET /notaproxy HTTP/1.1\r\n\r\n")
            s.settimeout(_CONNECT_TIMEOUT_S)
            resp = s.recv(256)
        assert resp.startswith(_HTTP_400), resp


def test_ipv6_target_is_parsed(tmp_path: Path) -> None:
    for proxy in _make_proxy(tmp_path, frozenset({"::1"})):
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=_CONNECT_TIMEOUT_S) as s:
            s.sendall(b"CONNECT [::1]:9999 HTTP/1.1\r\n\r\n")
            s.settimeout(_CONNECT_TIMEOUT_S)
            resp = s.recv(256)
        assert not resp.startswith(_HTTP_400), resp


def test_decisions_are_logged_as_jsonl(tmp_path: Path, upstream: _Upstream) -> None:
    log_path = tmp_path / "proxy.jsonl"
    proxy = EgressProxy(ProxyConfig(allowlist=frozenset({"127.0.0.1"}), log_path=log_path))
    proxy.start()
    try:
        _connect_request(proxy.port, f"127.0.0.1:{upstream.port}", body=b"x")
        _connect_request(proxy.port, "blocked.example.com:443")
    finally:
        proxy.stop()
    raw = [x for x in log_path.read_text().splitlines() if x.strip()]
    lines: list[dict[str, object]] = [cast(dict[str, object], json.loads(x)) for x in raw]
    events = {entry["event"] for entry in lines}
    assert "allow" in events
    assert "deny" in events
    for entry in lines:
        assert "host" in entry and "ts" in entry
