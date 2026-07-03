from __future__ import annotations

import subprocess

from common.errors import LifecycleError
from harness.shell import TmuxShell


class FakeWaitTimeoutShell(TmuxShell):
    def __init__(self, *, include_marker: bool = True) -> None:
        self._session_name = "fake"
        self._truncate_bytes = 10 * 1024
        self._closed = False
        self.include_marker = include_marker

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
        current = f"\n{marker}0" if self.include_marker else ""
        return f"old command\n__rc__99\n{self.sent}\npartial output{current}\n"


def test_blocking_wait_timeout_recovers_if_status_marker_is_present() -> None:
    shell = FakeWaitTimeoutShell()

    result = shell.run("echo recovered", duration_sec=0.0, is_blocking=True)

    assert "partial output" in result.terminal_output
    assert result.exit_status == 0


def test_blocking_wait_timeout_without_status_marker_returns_partial_output() -> None:
    shell = FakeWaitTimeoutShell(include_marker=False)

    result = shell.run("sleep 999", duration_sec=0.0, is_blocking=True)

    assert "partial output" in result.terminal_output
    assert result.exit_status is None
