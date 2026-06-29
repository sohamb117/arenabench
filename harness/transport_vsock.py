"""AF_VSOCK transport for the harness ↔ orchestrator channel.

Plan §3.A3 chose vsock as the primary transport. Plan R2 documents that vsock
is unavailable on macOS Docker Desktop / colima at the time of v1, so the
runtime fallback is `harness.transport_ssh.SshTransport`. This module exists
for hosts where `/dev/vhost-vsock` IS exposed (Linux + kvm, or a future
Docker Desktop release that exposes it).

Both transports implement the structural `harness.transport.Transport`
Protocol (typing.Protocol), so the harness loop accepts either without
modification. The orchestrator chooses between them at boot via
`scripts/probe-vsock.sh`.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

from common.errors import TransportError
from common.protocol import (
    MAX_FRAME_BYTES,
    Envelope,
    parse_envelope,
    serialize_envelope,
)


@dataclass(frozen=True, slots=True)
class VsockConfig:
    cid: int
    port: int
    connect_timeout_s: float = 30.0


class VsockTransport:
    """AF_VSOCK SOCK_STREAM client transport.

    The orchestrator's vsock_server listens on the configured (cid, port);
    each agent opens ONE long-lived connection from inside the guest VM.

    Note: on macOS Docker Desktop / colima this raises OSError at open()
    because AF_VSOCK is not in the kernel's socket-family table. The R2
    fallback (transport_ssh.SshTransport) covers that case.
    """

    def __init__(self, cfg: VsockConfig) -> None:
        self._cfg = cfg
        self._sock: socket.socket | None = None
        self._buf = b""

    def open(self) -> None:
        if not hasattr(socket, "AF_VSOCK"):
            raise TransportError("AF_VSOCK is not available on this host")
        family = socket.AF_VSOCK
        self._sock = socket.socket(family, socket.SOCK_STREAM)
        self._sock.settimeout(self._cfg.connect_timeout_s)
        try:
            self._sock.connect((self._cfg.cid, self._cfg.port))
        except OSError as exc:
            self._sock.close()
            self._sock = None
            raise TransportError(f"vsock connect failed: {exc}") from exc
        self._sock.settimeout(None)

    def send(self, env: Envelope) -> None:
        if self._sock is None:
            raise TransportError("vsock transport not open")
        line = serialize_envelope(env).encode("utf-8") + b"\n"
        self._sock.sendall(line)

    def recv(self, timeout_s: float | None = None) -> Envelope | None:
        if self._sock is None:
            return None
        self._sock.settimeout(timeout_s)
        try:
            while b"\n" not in self._buf:
                chunk = self._sock.recv(MAX_FRAME_BYTES)
                if not chunk:
                    return None
                self._buf += chunk
        except TimeoutError:
            return None
        line, self._buf = self._buf.split(b"\n", 1)
        return parse_envelope(line.decode("utf-8"))

    def is_open(self) -> bool:
        return self._sock is not None

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
