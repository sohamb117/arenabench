from __future__ import annotations

import subprocess

from harness.shell import TmuxShell


class _FakeSendShell(TmuxShell):
    """Fake shell that captures the string handed to `_send_command`.

    Used to assert the exact bytes tmux would type into the pane, since
    real tmux may not be available in every CI environment.
    """

    def __init__(self) -> None:
        self._session_name = "fake"
        self._truncate_bytes = 10 * 1024
        self._closed = False
        self.sent: str | None = None

    def _run_tmux(
        self,
        args: list[str],
        *,
        timeout_s: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        _ = timeout_s
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    def _send_command(self, command: str) -> None:
        self.sent = command

    def is_alive(self) -> bool:
        return True

    def capture_pane(self) -> str:
        if self.sent is None:
            return ""
        marker = self.sent.split('echo "', maxsplit=1)[1].split('$?"', maxsplit=1)[0]
        return f"{self.sent}\n{marker}0\n"


def test_sentinel_stays_on_same_line_when_keystrokes_end_with_newline() -> None:
    """Regression: LLM parsers often emit keystrokes with a trailing '\\n'.
    Before the fix, tmux hits Enter mid-string and the sentinel arrives
    at a fresh prompt starting with a bare ';' → 'bash: syntax error'.
    """
    shell = _FakeSendShell()

    shell.run("echo hello\n", duration_sec=0.0, is_blocking=True)

    assert shell.sent is not None
    # The sentinel must not be preceded by a raw newline in the sent
    # string. Any raw newline would cause tmux to submit the command
    # early and the sentinel would be typed at a fresh prompt.
    assert "\n;" not in shell.sent
    assert shell.sent.startswith("echo hello;")


def test_sentinel_does_not_produce_double_semicolon_when_keystrokes_end_with_semicolon() -> None:
    """Trailing ';' in keystrokes must not collide with the sentinel's
    leading ';' — bash rejects ';;' outside a case terminator.
    """
    shell = _FakeSendShell()

    shell.run("echo hi;", duration_sec=0.0, is_blocking=True)

    assert shell.sent is not None
    assert ";;" not in shell.sent
    assert shell.sent.startswith("echo hi;")


def test_sentinel_handles_trailing_whitespace_and_semicolon_mix() -> None:
    """Whitespace-and-semicolon mixes ('foo ; ', 'foo;\\n', 'foo\\n;')
    must all collapse to a clean single-separator boundary.
    """
    for keystrokes in ["echo x ;\n", "echo x;\n", "echo x\n;", "echo x  ;  "]:
        shell = _FakeSendShell()

        shell.run(keystrokes, duration_sec=0.0, is_blocking=True)

        assert shell.sent is not None, keystrokes
        assert "\n;" not in shell.sent, keystrokes
        assert ";;" not in shell.sent, keystrokes
        assert shell.sent.startswith("echo x;"), keystrokes


def test_command_result_preserves_original_keystrokes() -> None:
    """CommandResult.keystrokes must reflect the LLM's exact bytes, even
    though the sentinel path strips trailing separators internally.
    """
    shell = _FakeSendShell()

    result = shell.run("echo hello\n", duration_sec=0.0, is_blocking=True)

    assert result.keystrokes == "echo hello\n"
