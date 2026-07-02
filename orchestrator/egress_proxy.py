"""Host-side HTTP CONNECT egress proxy with domain allowlisting (plan T6).

Replaces the guest's fragile IP-pinning firewall. LLM agents in the QEMU VM
reach this proxy over the slirp gateway (10.0.2.2); the proxy gates outbound
HTTPS by DOMAIN (exact, case-insensitive), resolves each allowed host ONCE
(DNS-rebinding defence), then tunnels ciphertext end-to-end. It never sees
plaintext — the guest's TLS is verified against the real target host.

Runs its asyncio server on a dedicated thread so synchronous orchestrator
code (`_drive_match`) can start()/stop() it around a match.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast

from common.clock import now_utc
from orchestrator import _egress_proxy_io as io

_HEADER_TIMEOUT_S = 5.0
_IDLE_TIMEOUT_S = 60.0
_TOTAL_TIMEOUT_S = 900.0
_START_TIMEOUT_S = 10.0
_STOP_TIMEOUT_S = 10.0
_HTTPS_PORT = 443


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    allowlist: frozenset[str]
    log_path: Path
    header_timeout_s: float = _HEADER_TIMEOUT_S
    idle_timeout_s: float = _IDLE_TIMEOUT_S
    total_timeout_s: float = _TOTAL_TIMEOUT_S


class EgressProxy:
    DEFAULT_ALLOWLIST: ClassVar[frozenset[str]] = frozenset(
        {
            "api.openai.com",
            "api.anthropic.com",
            "generativelanguage.googleapis.com",
            "api.mistral.ai",
            "api.deepseek.com",
        }
    )

    def __init__(self, cfg: ProxyConfig) -> None:
        self._cfg = cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: asyncio.Server | None = None
        self._thread: threading.Thread | None = None
        self._port = 0

    @property
    def port(self) -> int:
        return self._port

    def start(self) -> None:
        self._loop = asyncio.new_event_loop()
        ready = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self._thread.start()
        if not ready.wait(timeout=_START_TIMEOUT_S):
            raise RuntimeError("egress proxy failed to bind within timeout")

    def _run(self, ready: threading.Event) -> None:
        loop = self._loop
        if loop is None:
            return
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._bind(ready))
        loop.run_forever()

    async def _bind(self, ready: threading.Event) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        sockname = cast("tuple[str, int]", self._server.sockets[0].getsockname())
        self._port = sockname[1]
        ready.set()

    def stop(self) -> None:
        loop = self._loop
        if loop is None or self._thread is None:
            return
        asyncio.run_coroutine_threadsafe(self._shutdown(), loop).result(timeout=_STOP_TIMEOUT_S)
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=_STOP_TIMEOUT_S)
        loop.close()
        self._loop = None

    async def _shutdown(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(reader.readline(), self._cfg.header_timeout_s)
        except (TimeoutError, OSError):
            await io.respond(writer, b"400 Bad Request")
            return
        parsed = io.parse_connect(line)
        if parsed is None:
            self._log("malformed", "", 0)
            await io.respond(writer, b"400 Bad Request")
            return
        host, port = parsed
        await io.drain_headers(reader, self._cfg.header_timeout_s)
        normalized_host = host.lower()
        if normalized_host not in self._cfg.allowlist:
            self._log("deny", host, port)
            await io.respond(writer, b"403 Forbidden")
            return
        if port != _HTTPS_PORT:
            self._log("deny", host, port, reason="unsupported_port")
            await io.respond(writer, b"403 Forbidden")
            return
        await self._connect_and_tunnel(reader, writer, host, port)

    async def _connect_and_tunnel(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        host: str,
        port: int,
    ) -> None:
        candidates = await self._resolve_once(host, port)
        if not candidates:
            self._log("error", host, port, reason="resolve_failed")
            await io.respond(writer, b"502 Bad Gateway")
            return
        upstream = await self.connect_upstream(candidates, port)
        if upstream is None:
            self._log("error", host, port, reason="connect_failed")
            await io.respond(writer, b"502 Bad Gateway")
            return
        ip, up_reader, up_writer = upstream
        self._log("allow", host, port, ip=ip)
        await io.respond(writer, b"200 Connection established")
        await io.tunnel(
            reader,
            writer,
            up_reader,
            up_writer,
            idle_s=self._cfg.idle_timeout_s,
            total_s=self._cfg.total_timeout_s,
        )

    async def _resolve_once(self, host: str, port: int) -> list[tuple[int, str]]:
        """Resolve host ONCE and return all (family, ip) candidates.

        Connecting to one of these literal IPs (not the hostname) is the DNS-
        rebinding defence: the target cannot change between allowlist check and
        TCP connect. Trying every candidate handles IPv6-first DNS on hosts with
        IPv4-only reachability.
        """
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            )
        except OSError:
            return []
        return [(int(family), str(sockaddr[0])) for family, _, _, _, sockaddr in infos]

    async def connect_upstream(
        self, candidates: list[tuple[int, str]], port: int
    ) -> tuple[str, asyncio.StreamReader, asyncio.StreamWriter] | None:
        for family, ip in candidates:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host=ip, port=port, family=family),
                    self._cfg.header_timeout_s,
                )
                return ip, reader, writer
            except (OSError, TimeoutError):
                continue
        return None

    def _log(self, event: str, host: str, port: int, **extra: object) -> None:
        entry: dict[str, object] = {
            "ts": now_utc().isoformat(),
            "event": event,
            "host": host,
            "port": port,
            **extra,
        }
        with contextlib.suppress(OSError), self._cfg.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
