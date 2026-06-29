import shutil
import time
import uuid

import pytest

from harness.shell import TmuxShell

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")

_BLOCKING_DURATION_SEC = 0.5
_NON_BLOCKING_SLEEP_SEC = 0.2
_NON_BLOCKING_DURATION_SEC = 0.05
_MAX_DURATION_S = 5.0
_NON_BLOCKING_MAX_DURATION_S = 0.18
_TRUNCATE_BYTES = 10 * 1024
_LONG_OUTPUT_BYTES = 12 * 1024


def _session_name() -> str:
    return f"arenabench-test-{uuid.uuid4().hex[:8]}"


def test_run_blocking_echo_returns_output_and_status() -> None:
    with TmuxShell(_session_name()) as shell:
        result = shell.run(
            "echo hello",
            duration_sec=_BLOCKING_DURATION_SEC,
            is_blocking=True,
        )

    assert "hello" in result.terminal_output
    assert result.exit_status == 0
    assert 0 < result.duration_s < _MAX_DURATION_S


def test_persists_variable_across_turns() -> None:
    with TmuxShell(_session_name()) as shell:
        shell.run("X=42", duration_sec=_BLOCKING_DURATION_SEC, is_blocking=True)
        result = shell.run(
            "echo $X",
            duration_sec=_BLOCKING_DURATION_SEC,
            is_blocking=True,
        )

    assert "42" in result.terminal_output


def test_blocking_false_returns_exit_status_one() -> None:
    with TmuxShell(_session_name()) as shell:
        result = shell.run(
            "false",
            duration_sec=_BLOCKING_DURATION_SEC,
            is_blocking=True,
        )

    assert result.exit_status == 1


def test_non_blocking_returns_without_waiting_for_command() -> None:
    with TmuxShell(_session_name()) as shell:
        started_s = time.monotonic()
        result = shell.run(
            f"sleep {_NON_BLOCKING_SLEEP_SEC}",
            duration_sec=_NON_BLOCKING_DURATION_SEC,
            is_blocking=False,
        )
        elapsed_s = time.monotonic() - started_s

    assert result.exit_status is None
    assert elapsed_s < _NON_BLOCKING_MAX_DURATION_S


def test_truncates_large_terminal_output() -> None:
    with TmuxShell(_session_name(), truncate_bytes=_TRUNCATE_BYTES) as shell:
        result = shell.run(
            f"python3 -c \"print('a' * {_LONG_OUTPUT_BYTES})\"",
            duration_sec=_BLOCKING_DURATION_SEC,
            is_blocking=True,
        )

    assert len(result.terminal_output.encode()) <= _TRUNCATE_BYTES
    assert result.truncated_bytes > 0


def test_lifecycle_close_kills_session() -> None:
    shell = TmuxShell(_session_name())
    try:
        assert shell.is_alive()
        shell.close()
        assert not shell.is_alive()
    finally:
        shell.close()


def test_context_manager_cleans_up_session() -> None:
    name = _session_name()
    with TmuxShell(name) as shell:
        assert shell.is_alive()

    assert not shell.is_alive()
