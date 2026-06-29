"""Sync JSONL socket server; selectors multiplex listeners without per-port threads."""

from __future__ import annotations

import os
import selectors
import socket
import time
from copy import copy
from pathlib import Path
from typing import Protocol

from common.errors import TransportError
from common.log import get_logger
from common.protocol import MAX_FRAME_BYTES, Envelope, parse_envelope, serialize_envelope

PROBE_PORT = 9999
FIRST_AGENT_PORT = 10000
LISTEN_BACKLOG = 2
_ENCODING = "utf-8"


class SocketBackend(Protocol):
    def listen(self, port: int) -> None: ...
    def accept(self, timeout_s: float | None = None) -> ServerConn | None: ...
    def close(self) -> None: ...
    def fileno(self) -> int: ...


class ServerConn(Protocol):
    def recv(self, timeout_s: float | None = None) -> Envelope | None: ...
    def send(self, env: Envelope) -> None: ...
    def close(self) -> None: ...

    @property
    def remote_addr(self) -> str: ...


class _UnixServerConn:
    def __init__(self, sock: socket.socket, remote_addr: str) -> None:
        self._sock = sock
        self._remote_addr = remote_addr
        self._buffer = bytearray()

    @property
    def remote_addr(self) -> str:
        return self._remote_addr

    def recv(self, timeout_s: float | None = None) -> Envelope | None:
        self._sock.settimeout(timeout_s)
        try:
            while b"\n" not in self._buffer:
                chunk = self._sock.recv(MAX_FRAME_BYTES + 1)
                if not chunk:
                    return None
                self._buffer.extend(chunk)
                if len(self._buffer) > MAX_FRAME_BYTES and b"\n" not in self._buffer:
                    raise TransportError("frame exceeds max bytes", remote_addr=self._remote_addr)
        except TimeoutError:
            return None
        line_bytes, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)
        try:
            return parse_envelope(line_bytes.decode(_ENCODING))
        except (UnicodeDecodeError, ValueError) as exc:
            raise TransportError("invalid frame", remote_addr=self._remote_addr) from exc

    def send(self, env: Envelope) -> None:
        try:
            self._sock.sendall(f"{serialize_envelope(env)}\n".encode(_ENCODING))
        except OSError as exc:
            raise TransportError("send failed", remote_addr=self._remote_addr) from exc

    def close(self) -> None:
        self._sock.close()


class UnixSocketBackend:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._listener: socket.socket | None = None
        self._path: Path | None = None

    def listen(self, port: int) -> None:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        path = self._base_dir / f"port-{port}.sock"
        path.unlink(missing_ok=True)
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        current_dir = Path.cwd()
        try:
            os.chdir(self._base_dir)
            listener.bind(path.name)
        finally:
            os.chdir(current_dir)
        listener.listen(LISTEN_BACKLOG)
        listener.setblocking(False)
        self._listener = listener
        self._path = path

    def fileno(self) -> int:
        return -1 if self._listener is None else self._listener.fileno()

    def accept(self, timeout_s: float | None = None) -> ServerConn | None:
        listener = self._require_listener()
        selector = selectors.DefaultSelector()
        try:
            selector.register(listener, selectors.EVENT_READ)
            if not selector.select(timeout_s):
                return None
        finally:
            selector.close()
        try:
            conn = listener.accept()[0]
        except BlockingIOError:
            return None
        conn.setblocking(True)
        return _UnixServerConn(conn, "unix-client")

    def close(self) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._path is not None:
            self._path.unlink(missing_ok=True)
            self._path = None

    def _require_listener(self) -> socket.socket:
        if self._listener is None:
            raise TransportError("listener is not started")
        return self._listener


class VsockServer:
    def __init__(self, backend: SocketBackend, n_agents: int) -> None:
        if n_agents < 1:
            raise ValueError("n_agents must be positive")
        self._template = backend
        self._n_agents = n_agents
        self._ports = [*(FIRST_AGENT_PORT + index for index in range(n_agents)), PROBE_PORT]
        self._listeners: dict[int, SocketBackend] = {}
        self._ports_by_fd: dict[int, int] = {}
        self._conns: dict[int, ServerConn] = {}
        self._selector = selectors.DefaultSelector()
        self._logger = get_logger(__name__)

    @property
    def n_agents(self) -> int:
        return self._n_agents

    def start(self) -> None:
        for port in self._ports:
            listener = copy(self._template)
            listener.listen(port)
            self._listeners[port] = listener
            self._ports_by_fd[listener.fileno()] = port
            self._selector.register(listener, selectors.EVENT_READ)

    def stop(self) -> None:
        for conn in self._conns.values():
            conn.close()
        self._conns.clear()
        self._selector.close()
        self._selector = selectors.DefaultSelector()
        for listener in self._listeners.values():
            listener.close()
        self._listeners.clear()
        self._ports_by_fd.clear()

    def wait_for_connect(self, port: int, timeout_s: float) -> ServerConn:
        self._require_port(port)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() <= deadline:
            if port in self._conns:
                return self._conns[port]
            for key, _ in self._selector.select(max(0.0, deadline - time.monotonic())):
                self._accept_ready(self._ports_by_fd[key.fd])
            if port in self._conns:
                return self._conns[port]
        raise TransportError("connection timed out", port=port)

    def recv_frame(self, port: int, timeout_s: float | None) -> Envelope | None:
        if port not in self._conns:
            self._poll_accept(port)
        conn = self._conns.get(port)
        if conn is None:
            return None
        frame = conn.recv(timeout_s)
        if frame is None:
            self._close_conn(port)
        return frame

    def _poll_accept(self, port: int) -> None:
        if port not in self._listeners:
            return
        try:
            self._accept_ready(port)
        except (BlockingIOError, OSError):
            return

    def send_frame(self, port: int, env: Envelope) -> None:
        conn = self._conns.get(port)
        if conn is None:
            raise TransportError("no active connection", port=port)
        conn.send(env)

    def is_open(self, port: int) -> bool:
        return port in self._conns

    def _accept_ready(self, port: int) -> None:
        conn = self._listeners[port].accept(0.0)
        if conn is None:
            return
        if port in self._conns:
            self._logger.warning("duplicate socket connection closed", port=port)
            conn.close()
            return
        self._conns[port] = conn

    def _close_conn(self, port: int) -> None:
        conn = self._conns.pop(port, None)
        if conn is not None:
            conn.close()

    def _require_port(self, port: int) -> None:
        if port not in self._listeners:
            raise TransportError("port is not listening", port=port)
