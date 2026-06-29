from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import harness.shell
from common.protocol import HeartbeatTick, Shutdown
from tests.integration._harness_loop_fakes import (
    SLOT,
    FakeShell,
    drain_until_exit,
    make_response,
    recv,
    run_harness_thread,
    send,
    two_complete,
)

MAX_CONTEXT_TOKENS = 200
EXPECTED_HEARTBEAT_MESSAGES = 3
EXPECTED_CONFIRMATION_CALLS = 2


@pytest.fixture(autouse=True)
def patch_shell(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    FakeShell.calls = []
    monkeypatch.setattr(harness.shell, "TmuxShell", FakeShell)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    yield


def test_pid_announce_first_frame(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = run_harness_thread(tmp_path, monkeypatch, two_complete())
    first = recv(run.peer)
    send(run.peer, Shutdown(reason="stop"))
    drain_until_exit(run)
    assert first.kind == "pid_announce"
    assert first.src == f"agent{SLOT}"
    assert first.seq == 0
    assert first.data.kind == "pid_announce"
    assert first.data.parser == "json"
    assert first.data.model == "test/model"


def test_one_llm_call_bash_frame_sequence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [make_response(command="whoami", task_complete=True), make_response(task_complete=True)],
    )
    frames = drain_until_exit(run)
    assert [frame.kind for frame in frames] == [
        "pid_announce",
        "llm_request",
        "llm_response",
        "bash_request",
        "bash_result",
        "turn_summary",
        "llm_request",
        "llm_response",
        "turn_summary",
        "harness_exit",
    ]
    assert FakeShell.calls == ["whoami"]


def test_heartbeat_injected_before_next_llm_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [make_response(), make_response(task_complete=True), make_response(task_complete=True)],
    )
    first_turn = [recv(run.peer) for _ in range(4)]
    send(run.peer, HeartbeatTick(elapsed_s=12.0, turn_hint=1))
    heartbeat = recv(run.peer)
    request = recv(run.peer)
    send(run.peer, Shutdown(reason="stop"))
    drain_until_exit(run)
    assert first_turn[-1].kind == "turn_summary"
    assert heartbeat.kind == "heartbeat_injected"
    assert heartbeat.data.kind == "heartbeat_injected"
    assert heartbeat.data.payload.startswith("[HEARTBEAT t=")
    assert request.kind == "llm_request"
    assert request.data.kind == "llm_request"
    assert request.data.messages_count == EXPECTED_HEARTBEAT_MESSAGES


def test_parser_json_happy_path_sets_parse_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(tmp_path, monkeypatch, two_complete())
    response = next(frame for frame in drain_until_exit(run) if frame.kind == "llm_response")
    assert response.data.kind == "llm_response"
    assert response.data.parse_ok is True


def test_chat_trim_observable_in_turn_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [make_response(command="printf overflow") for _ in range(3)]
        + [make_response(task_complete=True)],
        max_tokens=MAX_CONTEXT_TOKENS,
    )
    summaries = [frame for frame in drain_until_exit(run) if frame.kind == "turn_summary"]
    assert any(frame.data.kind == "turn_summary" and frame.data.summarized for frame in summaries)


def test_crash_emits_harness_exit_code_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = run_harness_thread(tmp_path, monkeypatch, [make_response(), RuntimeError("boom")])
    terminal = drain_until_exit(run)[-1]
    assert terminal.data.kind == "harness_exit"
    assert terminal.data.reason == "crash"
    assert terminal.data.code == 1


def test_retry_exception_from_llm_call_does_not_emit_intermediate_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(tmp_path, monkeypatch, two_complete())
    kinds = [frame.kind for frame in drain_until_exit(run)]
    assert kinds.count("llm_request") == EXPECTED_CONFIRMATION_CALLS
    assert kinds.count("llm_response") == EXPECTED_CONFIRMATION_CALLS
    assert kinds[-1] == "harness_exit"


def test_task_complete_double_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = run_harness_thread(tmp_path, monkeypatch, two_complete())
    frames = drain_until_exit(run)
    assert [frame.kind for frame in frames].count("llm_request") == EXPECTED_CONFIRMATION_CALLS
    assert frames[-1].data.kind == "harness_exit"
    assert frames[-1].data.reason == "clean"
    assert frames[-1].data.code == 0


def test_shutdown_frame_exits_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = run_harness_thread(tmp_path, monkeypatch, [make_response(task_complete=True)])
    recv(run.peer)
    send(run.peer, Shutdown(reason="stop"))
    frames = drain_until_exit(run)
    assert frames[-1].data.kind == "harness_exit"
    assert frames[-1].data.reason == "shutdown_received"
    assert frames[-1].data.code == 0
