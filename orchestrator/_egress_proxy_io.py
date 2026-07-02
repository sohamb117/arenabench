"""Async I/O helpers for the CONNECT egress proxy (plan T6).

Split from egress_proxy.py to keep both files under the 250 LOC cap. These
functions own CONNECT-line parsing (incl. IPv6 `[::1]:443`), the tunnel pump
with half-close, and status replies. No allowlist / lifecycle logic lives here.
"""

from __future__ import annotations

import asyncio
import contextlib

_MAX_AUTHORITY = 512
_PORT_MIN = 0
_PORT_MAX = 65536
_READ_CHUNK = 65536
_CONNECT_TOKENS = 3


def _split_authority(authority: str) -> tuple[str, int] | None:
    """Split a CONNECT authority into (host, port), handling `[::1]:443`."""
    if authority.startswith("["):
        end = authority.find("]")
        if end == -1 or not authority[end + 1 :].startswith(":"):
            return None
        host, port_s = authority[1:end], authority[end + 2 :]
    elif ":" in authority:
        host, _, port_s = authority.rpartition(":")
    else:
        return None
    if not host or not port_s.isdigit():
        return None
    port = int(port_s)
    if not (_PORT_MIN < port < _PORT_MAX):
        return None
    return host, port


def parse_connect(line: bytes) -> tuple[str, int] | None:
    """Parse a `CONNECT host:port HTTP/1.1` request line.

    Returns (host, port) or None for anything malformed. Handles bracketed
    IPv6 authorities (`[::1]:443`); rejects non-CONNECT methods.
    """
    if len(line) > _MAX_AUTHORITY:
        return None
    try:
        text = line.decode("latin-1").strip()
    except ValueError:
        return None
    parts = text.split(" ")
    if len(parts) != _CONNECT_TOKENS or parts[0] != "CONNECT":
        return None
    return _split_authority(parts[1])


async def respond(writer: asyncio.StreamWriter, status: bytes) -> None:
    """Write an `HTTP/1.1 <status>\\r\\n\\r\\n` reply; close on non-200."""
    writer.write(b"HTTP/1.1 " + status + b"\r\n\r\n")
    with contextlib.suppress(OSError):
        await writer.drain()
    if not status.startswith(b"200"):
        writer.close()


async def drain_headers(reader: asyncio.StreamReader, timeout_s: float) -> None:
    """Consume the remaining request headers up to the terminating blank line."""
    try:
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout_s)
            if line in (b"\r\n", b"\n", b""):
                return
    except (TimeoutError, OSError):
        return


async def _pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter, idle_s: float) -> None:
    try:
        while True:
            data = await asyncio.wait_for(src.read(_READ_CHUNK), idle_s)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except (OSError, TimeoutError):
        pass
    finally:
        with contextlib.suppress(OSError):
            if dst.can_write_eof():
                dst.write_eof()


async def tunnel(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_reader: asyncio.StreamReader,
    upstream_writer: asyncio.StreamWriter,
    *,
    idle_s: float,
    total_s: float,
) -> None:
    """Bidirectionally pipe client<->upstream with half-close + total cap."""
    both = asyncio.gather(
        _pump(client_reader, upstream_writer, idle_s),
        _pump(upstream_reader, client_writer, idle_s),
    )
    try:
        await asyncio.wait_for(both, total_s)
    except (TimeoutError, OSError):
        both.cancel()
    finally:
        for w in (client_writer, upstream_writer):
            with contextlib.suppress(OSError):
                w.close()
