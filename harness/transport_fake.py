"""In-memory transport for tests."""

from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue

from common.errors import TransportError
from common.protocol import Envelope

_CLOSED = object()


@dataclass
class _Link:
    a_in: Queue[Envelope | object]
    b_in: Queue[Envelope | object]
    open: bool = True


class InMemoryTransport:
    """Two FIFO queues backed by queue.Queue.

    send() pushes to the peer inbound queue; recv() pops from the local inbound
    queue. Use peer() to obtain the mirror transport so two endpoints can talk.
    """

    def __init__(self, _link: _Link | None = None, _is_a: bool = True) -> None:
        if _link is None:
            _link = _Link(Queue(), Queue())
        self._link = _link
        self._is_a = _is_a
        self._peer = InMemoryTransport(_link, not _is_a) if _is_a else None
        if self._peer is not None:
            self._peer._peer = self

    def peer(self) -> InMemoryTransport:
        return self._peer if self._peer is not None else self

    def send(self, env: Envelope) -> None:
        if not self.is_open():
            raise TransportError("transport is closed")
        (self._link.b_in if self._is_a else self._link.a_in).put(env)

    def recv(self, timeout_s: float | None = None) -> Envelope | None:
        inbound = self._link.a_in if self._is_a else self._link.b_in
        if not self.is_open() and inbound.empty():
            return None
        try:
            if timeout_s is None:
                item = inbound.get(block=True)
            else:
                item = inbound.get(block=True, timeout=timeout_s)
        except Empty:
            return None
        if item is _CLOSED:
            return None
        assert isinstance(item, Envelope)
        return item

    def is_open(self) -> bool:
        return self._link.open

    def close(self) -> None:
        if not self._link.open:
            return
        self._link.open = False
        self._link.a_in.put(_CLOSED)
        self._link.b_in.put(_CLOSED)
