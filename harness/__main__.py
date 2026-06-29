"""Run the arenabench harness inside a guest VM.

Usage (inside the guest):

    python3 -m harness /home/<user>/config.json

The harness reads its configuration from the config.json path, opens stdin/stdout
as the JSONL transport (one envelope per line), and runs the loop until clean exit,
crash, or shutdown frame received.

This is invoked by orchestrator/ssh_server.py over an SSH session whose stdin/stdout
are the wire — the same channel that vsock would carry in a vsock-enabled host.
"""

from __future__ import annotations

import getpass
import os
import selectors
import sys
import time
from pathlib import Path

from common.errors import TransportError
from common.protocol import (
    MAX_FRAME_BYTES,
    Envelope,
    parse_envelope,
    serialize_envelope,
)
from harness.loop import run_harness
from harness.transport import Transport

_MIN_ARGS_WITH_TEMPLATE = 2


class StdioTransport:
    """Transport that reads/writes JSONL on stdin/stdout. Used by `python3 -m harness`.

    The orchestrator's `SshOrchestratorServer` opens an ssh subprocess that runs
    `python3 -m harness <config>`. The ssh stdin/stdout pipes are this transport's wire.
    `recv` is non-blocking with timeout support via selectors+os.read, so
    `harness.loop._process_pending` can poll without stalling between LLM turns.
    """

    def __init__(self) -> None:
        self._stdin_fd = sys.stdin.fileno()
        self._stdout = sys.stdout.buffer
        self._buffer = bytearray()
        self._closed = False
        self._selector = selectors.DefaultSelector()
        self._selector.register(self._stdin_fd, selectors.EVENT_READ)

    def send(self, env: Envelope) -> None:
        if self._closed:
            raise TransportError("stdio transport closed")
        line = serialize_envelope(env).encode("utf-8") + b"\n"
        self._stdout.write(line)
        self._stdout.flush()

    def recv(self, timeout_s: float | None = None) -> Envelope | None:
        if self._closed:
            return None
        deadline = None if timeout_s is None else time.monotonic() + timeout_s
        while b"\n" not in self._buffer:
            remaining_s = None if deadline is None else max(0.0, deadline - time.monotonic())
            if not self._selector.select(remaining_s):
                return None
            chunk = os.read(self._stdin_fd, MAX_FRAME_BYTES)
            if not chunk:
                self._closed = True
                return None
            self._buffer.extend(chunk)
            if len(self._buffer) > MAX_FRAME_BYTES and b"\n" not in self._buffer:
                raise TransportError("stdio frame exceeds max bytes")
        line, _, remainder = self._buffer.partition(b"\n")
        self._buffer = bytearray(remainder)
        return parse_envelope(line.decode("utf-8"))

    def is_open(self) -> bool:
        return not self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._selector.close()


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) < 1:
        sys.stderr.write("usage: python3 -m harness <config.json> [<initial_template_path>]\n")
        return 2
    config_path = Path(args[0])
    if len(args) >= _MIN_ARGS_WITH_TEMPLATE:
        initial_template = Path(args[1]).read_text(encoding="utf-8")
    else:
        prompt = config_path.parent / "system_prompt.txt"
        initial_template = prompt.read_text(encoding="utf-8") if prompt.is_file() else "Begin."
    transport: Transport = StdioTransport()
    slot = _slot_from_user()
    return run_harness(
        config_path=config_path,
        transport=transport,
        initial_template=initial_template,
        slot=slot,
        base_dir=config_path.parent,
    )


def _slot_from_user() -> int:
    user = getpass.getuser()
    if user.startswith("agent"):
        try:
            return int(user.removeprefix("agent"))
        except ValueError:
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
