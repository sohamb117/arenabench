"""Async-free transport contract for harness↔orchestrator channels."""

from typing import Protocol

from common.protocol import Envelope


class Transport(Protocol):
    """Async-free transport for the harness↔orchestrator vsock/unix channel.

    Implementations: transport_fake.InMemoryTransport (tests),
    transport_vsock.VsockTransport (W4.4/6.5 real backend),
    transport_unix.UnixSocketTransport (R2 fallback).
    """

    def send(self, env: Envelope) -> None: ...

    def recv(self, timeout_s: float | None = None) -> Envelope | None: ...

    def is_open(self) -> bool: ...

    def close(self) -> None: ...
