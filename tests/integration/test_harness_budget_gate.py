from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import harness.shell
from common.protocol import HarnessExit, HeartbeatTick, LlmRequest, LlmReservationDecision, Shutdown
from tests.integration._harness_loop_fakes import (
    FakeShell,
    drain_until_exit,
    make_response,
    recv,
    run_harness_thread,
    send,
)

MAX_OUTPUT_TOKENS = 10_000


@pytest.fixture(autouse=True)
def patch_shell(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    FakeShell.calls = []
    monkeypatch.setattr(harness.shell, "TmuxShell", FakeShell)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    yield


def test_enabled_budget_gate_emits_quote_inputs_before_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [make_response(task_complete=True)],
        budget_enabled=True,
        max_turns=1,
    )
    pid = recv(run.peer)
    request = recv(run.peer)

    assert pid.data.kind == "pid_announce"
    assert pid.data.budget_capability_version == 1
    assert request.data.kind == "llm_request"
    assert request.data.prompt_tokens is not None
    assert request.data.max_output_tokens == MAX_OUTPUT_TOKENS
    assert request.data.fallback_models is None
    send(
        run.peer,
        LlmReservationDecision(
            request_id=request.data.request_id,
            attempt=request.data.attempt,
            granted=False,
            reason="global_budget_exhausted",
        ),
    )
    frames = drain_until_exit(run)
    assert frames[-1].data.kind == "harness_exit"
    assert frames[-1].data.reason == "llm_fatal"
    assert run.captured_messages == []


def test_budget_gate_rejects_mismatched_decision_before_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(
        tmp_path, monkeypatch, [make_response(task_complete=True)], budget_enabled=True
    )
    recv(run.peer)
    request = recv(run.peer)
    assert isinstance(request.data, LlmRequest)
    assert isinstance(request.data, LlmRequest)
    assert request.data.kind == "llm_request"
    send(
        run.peer,
        LlmReservationDecision(
            request_id="different",
            attempt=request.data.attempt,
            granted=True,
            reason="granted",
            reserved_nano_usd=1,
        ),
    )

    frames = drain_until_exit(run)

    assert frames[-1].data.kind == "harness_exit"
    assert isinstance(frames[-1].data, HarnessExit)
    assert frames[-1].data.reason == "llm_fatal"
    assert run.captured_messages == []


def test_budget_gate_shutdown_before_decision_makes_zero_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(
        tmp_path, monkeypatch, [make_response(task_complete=True)], budget_enabled=True
    )
    recv(run.peer)
    recv(run.peer)
    send(run.peer, Shutdown(reason="budget_exhausted"))

    frames = drain_until_exit(run)

    assert frames[-1].data.kind == "harness_exit"
    assert run.captured_messages == []


def test_budget_gate_timeout_before_decision_makes_zero_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("harness.loop._RESERVATION_TIMEOUT_S", 0.01)
    run = run_harness_thread(
        tmp_path, monkeypatch, [make_response(task_complete=True)], budget_enabled=True
    )
    recv(run.peer)
    recv(run.peer)

    frames = drain_until_exit(run)

    assert frames[-1].data.kind == "harness_exit"
    assert frames[-1].data.reason == "llm_fatal"
    assert run.captured_messages == []


def test_budget_gate_channel_close_before_decision_makes_zero_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(
        tmp_path, monkeypatch, [make_response(task_complete=True)], budget_enabled=True
    )
    recv(run.peer)
    recv(run.peer)

    run.peer.close()
    run.thread.join(timeout=3.0)

    assert not run.thread.is_alive()
    assert run.captured_messages == []


def test_budget_gate_handles_heartbeat_before_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [make_response(task_complete=True)],
        budget_enabled=True,
        max_turns=1,
    )
    recv(run.peer)
    request = recv(run.peer)
    assert isinstance(request.data, LlmRequest)
    send(run.peer, HeartbeatTick(elapsed_s=1.0, turn_hint=0))
    send(
        run.peer,
        LlmReservationDecision(
            request_id=request.data.request_id,
            attempt=request.data.attempt,
            granted=True,
            reason="granted",
            reserved_nano_usd=1,
        ),
        seq=1,
    )

    frames = drain_until_exit(run)

    assert any(frame.data.kind == "heartbeat_injected" for frame in frames)
    assert len(run.captured_messages) == 1


def test_capability_timeout_fails_closed_before_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("harness.loop._CAPABILITY_TIMEOUT_S", 0.01)
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [make_response(task_complete=True)],
        send_capability=False,
    )
    recv(run.peer)

    frames = drain_until_exit(run)

    assert isinstance(frames[-1].data, HarnessExit)
    assert frames[-1].data.reason == "llm_fatal"
    assert run.captured_messages == []
