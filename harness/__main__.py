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
import sys
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
    """

    def __init__(self) -> None:
        self._stdin = sys.stdin.buffer
        self._stdout = sys.stdout.buffer
        self._closed = False

    def send(self, env: Envelope) -> None:
        if self._closed:
            raise TransportError("stdio transport closed")
        line = serialize_envelope(env).encode("utf-8") + b"\n"
        self._stdout.write(line)
        self._stdout.flush()

    def recv(self, timeout_s: float | None = None) -> Envelope | None:
        _ = timeout_s  # stdin is line-buffered; readline blocks regardless
        if self._closed:
            return None
        line = self._stdin.readline(MAX_FRAME_BYTES)
        if not line:
            self._closed = True
            return None
        return parse_envelope(line.decode("utf-8").rstrip("\n"))

    def is_open(self) -> bool:
        return not self._closed

    def close(self) -> None:
        self._closed = True


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
