"""Shared SSH transport test fakes used by test_transport_ssh.py and split files."""

from __future__ import annotations

import os

import pytest


class RecordingStdin:
    def __init__(self) -> None:
        self.data = bytearray()
        self.flush_count = 0
        self.closed = False

    def write(self, data: bytes) -> int:
        self.data.extend(data)
        return len(data)

    def flush(self) -> None:
        self.flush_count += 1

    def close(self) -> None:
        self.closed = True


class FakeSshProcess:
    def __init__(self) -> None:
        read_fd, write_fd = os.pipe()
        self.stdin = RecordingStdin()
        self.stdout = os.fdopen(read_fd, "rb", buffering=0)
        self._write_fd = write_fd
        self.terminated = False
        self.killed = False
        self.returncode: int | None = None

    def write_stdout(self, payload: bytes) -> None:
        _ = os.write(self._write_fd, payload)

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        return self.returncode if self.returncode is not None else 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def close_pipes(self) -> None:
        self.stdout.close()
        os.close(self._write_fd)


def patch_popen(
    monkeypatch: pytest.MonkeyPatch,
    process: FakeSshProcess,
) -> list[str]:
    captured: list[str] = []

    def fake_popen(argv: list[str], **kwargs: bool | int | None) -> FakeSshProcess:
        captured.extend(argv)
        assert kwargs["stdin"] is not None
        assert kwargs["stdout"] is not None
        assert kwargs["text"] is False
        assert kwargs["bufsize"] == 0
        return process

    monkeypatch.setattr("harness.transport_ssh.subprocess.Popen", fake_popen)
    return captured
