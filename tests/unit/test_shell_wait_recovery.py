from __future__ import annotations

import subprocess

from common.errors import LifecycleError
from harness.shell import TmuxShell


class FakeWaitTimeoutShell(TmuxShell):
    def __init__(self) -> None:
        self._session_name = "fake"
        self._truncate_bytes = 10 * 1024
        self._closed = False

    def _run_tmux(
        self,
        args: list[str],
        *,
        timeout_s: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        _ = timeout_s
        if args[0] == "wait":
            raise LifecycleError("tmux command timed out", state="active", event="wait")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def _send_command(self, command: str) -> None:
        self.sent = command

    def is_alive(self) -> bool:
        return True

    def capture_pane(self) -> str:
        marker = self.sent.split('echo "', maxsplit=1)[1].split('$?"', maxsplit=1)[0]
        return f"old command\n__rc__99\n{self.sent}\nrecovered\n{marker}0\n"


def test_blocking_wait_timeout_recovers_if_status_marker_is_present() -> None:
    shell = FakeWaitTimeoutShell()

    result = shell.run("echo recovered", duration_sec=0.0, is_blocking=True)

    assert "recovered" in result.terminal_output
    assert result.exit_status == 0
