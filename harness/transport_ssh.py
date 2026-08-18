from __future__ import annotations

import os
import selectors
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from common.errors import TransportError
from common.protocol import MAX_FRAME_BYTES, Envelope, parse_envelope, serialize_envelope

_GUEST_PYTHON = "/opt/arenabench-venv/bin/python3"
_OPEN_PROBE_TIMEOUT_S = 1.0


@dataclass(frozen=True, slots=True)
class SshConfig:
    host: str
    port: int
    user: str
    key_path: Path | None = None
    connect_timeout_s: float = 10.0
    keepalive_interval_s: float = 30.0
    remote_command: tuple[str, ...] | None = None


class SshTransport:
    """JSONL-over-SSH-stdin/stdout transport."""

    def __init__(self, cfg: SshConfig) -> None:
        self._cfg = cfg
        self._proc: subprocess.Popen[bytes] | None = None
        self._buffer = bytearray()

    def open(self) -> None:
        if self.is_open():
            return
        self._buffer.clear()
        self._proc = subprocess.Popen(
            self._argv(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=False,
            bufsize=0,
        )
        try:
            rc = self._proc.wait(timeout=_OPEN_PROBE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return
        raise self._error(f"ssh exited immediately with rc={rc}")

    def is_open(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def send(self, env: Envelope) -> None:
        proc = self._require_open()
        stdin = proc.stdin
        if stdin is None:
            raise self._error("ssh stdin is closed")
        try:
            stdin.write(f"{serialize_envelope(env)}\n".encode())
            stdin.flush()
        except OSError as exc:
            raise self._error("ssh send failed") from exc

    def recv(self, timeout_s: float | None = None) -> Envelope | None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return None
        stdout = proc.stdout
        if stdout is None:
            raise self._error("ssh stdout is closed")
        selector = selectors.DefaultSelector()
        try:
            selector.register(stdout, selectors.EVENT_READ)
            if not self._fill_until_line(selector, stdout.fileno(), timeout_s):
                return None
        finally:
            selector.close()
        line, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)
        try:
            return parse_envelope(line.decode())
        except (UnicodeDecodeError, ValueError) as exc:
            raise self._error("invalid ssh frame") from exc

    def close(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)
        for pipe in (proc.stdin, proc.stdout):
            if pipe is not None:
                pipe.close()
        self._proc = None
        self._buffer.clear()

    def __enter__(self) -> SshTransport:
        self.open()
        return self

    def __exit__(self, *args: object) -> None:
        _ = args
        self.close()

    def _argv(self) -> list[str]:
        key_arg = [] if self._cfg.key_path is None else ["-i", str(self._cfg.key_path)]
        if self._cfg.remote_command is None:
            # §3.B9 + Oracle round 19/20: API keys live in /home/agentN/.secrets
            # (mode 0600, owner agentN, seeded by cloud-init). Sourcing here keeps
            # the raw key out of ssh argv and /proc/<pid>/cmdline.
            # shlex.quote so the remote shell parses the semicolon-containing
            # body as a single argument to `bash -lc`, not as two statements.
            harness_cmd = (
                f". /home/{self._cfg.user}/.secrets 2>/dev/null; "
                "export LITELLM_LOCAL_MODEL_COST_MAP=True; "
                f"exec {_GUEST_PYTHON} -m harness /home/{self._cfg.user}/config.json"
            )
            command: list[str] = ["bash", "-lc", shlex.quote(harness_cmd)]
        else:
            command = list(self._cfg.remote_command)
        return [
            "ssh",
            "-p",
            str(self._cfg.port),
            *key_arg,
            "-o",
            f"ConnectTimeout={int(self._cfg.connect_timeout_s)}",
            "-o",
            f"ServerAliveInterval={int(self._cfg.keepalive_interval_s)}",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            f"{self._cfg.user}@{self._cfg.host}",
            *command,
        ]

    def _fill_until_line(
        self,
        selector: selectors.BaseSelector,
        fd: int,
        timeout_s: float | None,
    ) -> bool:
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while b"\n" not in self._buffer:
            remaining_s = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not selector.select(remaining_s):
                return False
            chunk = os.read(fd, MAX_FRAME_BYTES)
            if not chunk:
                return False
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_FRAME_BYTES and b"\n" not in self._buffer:
                raise self._error("ssh frame exceeds max bytes")
        return True

    def _require_open(self) -> subprocess.Popen[bytes]:
        if self._proc is None or self._proc.poll() is not None:
            raise self._error("ssh transport is closed")
        return self._proc

    def _error(self, message: str) -> TransportError:
        return TransportError(message, host=self._cfg.host, port=self._cfg.port)
