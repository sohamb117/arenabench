# tmux strategy adapted from terminal-bench Terminus 2
# (https://github.com/laude-institute/terminal-bench @ 1a6ffa9, Apache-2.0).
# Reimplemented for arenabench; no upstream code copied verbatim.

from __future__ import annotations

import re
import subprocess
import time
import uuid
from dataclasses import dataclass

from common.errors import LifecycleError

_TMUX = "tmux"
_RC_PREFIX = "__rc__"
_DEFAULT_WIDTH = "160"
_DEFAULT_HEIGHT = "40"


@dataclass(frozen=True, slots=True)
class CommandResult:
    keystrokes: str
    duration_s: float
    terminal_output: str
    truncated_bytes: int
    exit_status: int | None


class TmuxShell:
    """
    Persistent bash --login inside a tmux session.

    Run commands via `tmux send-keys`; blocking commands append
    `; tmux wait -S <token>` and wait for the matching `tmux wait` signal
    before capture. Non-blocking commands fire and sleep `duration_sec`.

    Mirror of Terminus 2's tmux strategy. Do NOT use subprocess.run() or
    pexpect — the persistent-context invariant requires tmux.
    """

    def __init__(
        self,
        session_name: str,
        *,
        bash_path: str = "/bin/bash",
        truncate_bytes: int = 10 * 1024,
        startup_timeout_s: float = 5.0,
    ) -> None:
        self._session_name = session_name
        self._truncate_bytes = truncate_bytes
        self._closed = False
        self._run_tmux(
            [
                "new-session",
                "-x",
                _DEFAULT_WIDTH,
                "-y",
                _DEFAULT_HEIGHT,
                "-d",
                "-s",
                session_name,
                bash_path,
                "--login",
            ],
            timeout_s=startup_timeout_s,
        )

    def __enter__(self) -> TmuxShell:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def is_alive(self) -> bool:
        result = subprocess.run(
            [_TMUX, "has-session", "-t", self._session_name],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def run(
        self,
        keystrokes: str,
        *,
        duration_sec: float,
        is_blocking: bool,
        wait_timeout_s: float = 30.0,
    ) -> CommandResult:
        self._require_alive("run")
        started_s = time.monotonic()
        exit_status: int | None = None

        if is_blocking:
            token = f"arenabench-done-{uuid.uuid4().hex[:8]}"
            marker = f"{_RC_PREFIX}{token}__"
            self._send_command(f'{keystrokes}; echo "{marker}$?"; tmux wait -S {token}')
            try:
                self._run_tmux(["wait", token], timeout_s=wait_timeout_s)
            except LifecycleError:
                raw_output = self.capture_pane()
                output, exit_status = self._extract_status(raw_output, marker)
                if exit_status is None:
                    raise
            else:
                raw_output = self.capture_pane()
                output, exit_status = self._extract_status(raw_output, marker)
        else:
            self._send_command(keystrokes)
            self._sleep_remaining(duration_sec, started_s)
            output = self.capture_pane()

        duration_s = time.monotonic() - started_s
        terminal_output, truncated_bytes = self._truncate(output)
        return CommandResult(
            keystrokes=keystrokes,
            duration_s=duration_s,
            terminal_output=terminal_output,
            truncated_bytes=truncated_bytes,
            exit_status=exit_status,
        )

    def capture_pane(self) -> str:
        self._require_alive("capture_pane")
        result = self._run_tmux(["capture-pane", "-p", "-S", "-", "-t", self._session_name])
        return result.stdout

    def close(self) -> None:
        if self._closed:
            return
        subprocess.run(
            [_TMUX, "kill-session", "-t", self._session_name],
            check=False,
            capture_output=True,
            text=True,
        )
        self._closed = True

    def _send_command(self, command: str) -> None:
        self._run_tmux(["send-keys", "-t", self._session_name, command, "Enter"])

    def _require_alive(self, event: str) -> None:
        if self._closed or not self.is_alive():
            raise LifecycleError("tmux session is not alive", state="closed", event=event)

    def _run_tmux(
        self,
        args: list[str],
        *,
        timeout_s: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [_TMUX, *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.CalledProcessError as exc:
            raise LifecycleError(
                "tmux command failed",
                state="closed" if self._closed else "active",
                event=args[0] if args else _TMUX,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise LifecycleError(
                "tmux command timed out",
                state="active",
                event=args[0] if args else _TMUX,
            ) from exc

    @staticmethod
    def _sleep_remaining(duration_sec: float, started_s: float) -> None:
        remaining_s = duration_sec - (time.monotonic() - started_s)
        if remaining_s > 0:
            time.sleep(remaining_s)

    @staticmethod
    def _extract_status(output: str, marker: str) -> tuple[str, int | None]:
        matches = list(re.finditer(rf"^{re.escape(marker)}(\d+)$", output, flags=re.MULTILINE))
        if not matches:
            return output, None
        match = matches[-1]
        exit_status = int(match.group(1))
        cleaned = f"{output[: match.start()]}{output[match.end() :]}"
        return cleaned, exit_status

    def _truncate(self, output: str) -> tuple[str, int]:
        encoded = output.encode()
        if len(encoded) <= self._truncate_bytes:
            return output, 0

        half = self._truncate_bytes // 2
        head = encoded[:half].decode(errors="ignore")
        tail = encoded[-half:].decode(errors="ignore")
        marker = "\n... [0 bytes truncated] ...\n"
        dropped = len(encoded) - len((head + marker + tail).encode())
        marker = f"\n... [{dropped} bytes truncated] ...\n"
        truncated = head + marker + tail
        while len(truncated.encode()) > self._truncate_bytes:
            tail = tail[1:]
            dropped += 1
            marker = f"\n... [{dropped} bytes truncated] ...\n"
            truncated = head + marker + tail
        return truncated, dropped
