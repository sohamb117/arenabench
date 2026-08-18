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
MIN_LLM_CALLS_FOR_HEARTBEAT_CHECK = 2
TURN_0_FRAME_COUNT = 7
TURN_WITHOUT_BASH_FRAME_COUNT = 5


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
        "llm_context_snapshot",
        "llm_response",
        "bash_request",
        "bash_result",
        "turn_summary",
        "llm_request",
        "llm_context_snapshot",
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
    first_turn = [recv(run.peer) for _ in range(TURN_WITHOUT_BASH_FRAME_COUNT)]
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
    # S4 plan §9 binary observable: the next llm_request's last user turn must
    # contain "[HEARTBEAT t=" — verified by inspecting the actual messages array
    # passed to litellm (captured by _harness_loop_fakes.fake_call).
    assert len(run.captured_messages) >= MIN_LLM_CALLS_FOR_HEARTBEAT_CHECK
    second_call_messages = run.captured_messages[1]
    assert second_call_messages[-1]["role"] == "user"
    assert "[HEARTBEAT t=" in second_call_messages[-1]["content"]


def test_heartbeat_merged_into_last_user_turn_when_chat_is_user_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: heartbeat ticks arriving during user-last state (~99%
    of match wall-clock, between bash_result and next LLM call) used to be
    silently dropped — 0 heartbeat_injected frames in a 300 s real match.
    Fix: merge into the last user turn instead of dropping, preserving the
    alternating-role invariant. S4 must be observable in BOTH chat states.
    """
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [
            make_response(command="whoami"),
            make_response(task_complete=True),
            make_response(task_complete=True),
        ],
    )
    turn0_frames = [recv(run.peer) for _ in range(TURN_0_FRAME_COUNT)]
    send(run.peer, HeartbeatTick(elapsed_s=15.0, turn_hint=0))
    heartbeat = recv(run.peer)
    request = recv(run.peer)
    send(run.peer, Shutdown(reason="stop"))
    drain_until_exit(run)

    assert turn0_frames[-1].kind == "turn_summary"
    assert turn0_frames[-2].kind == "bash_result"
    assert heartbeat.kind == "heartbeat_injected"
    assert heartbeat.data.kind == "heartbeat_injected"
    assert heartbeat.data.payload.startswith("[HEARTBEAT t=")
    assert request.kind == "llm_request"

    assert len(run.captured_messages) >= MIN_LLM_CALLS_FOR_HEARTBEAT_CHECK
    second_call_messages = run.captured_messages[1]
    last_user = second_call_messages[-1]
    assert last_user["role"] == "user"
    assert "[HEARTBEAT t=" in last_user["content"]
    # "\n\n[HEARTBEAT" proves the tick was merged INTO an existing user turn
    # (separator + heartbeat), not appended as a new user turn (which would
    # have produced a user-user pair and violated alternation).
    assert "\n\n[HEARTBEAT t=" in last_user["content"]


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
