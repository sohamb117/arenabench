from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import harness.shell
from common.protocol import (
    MAX_FRAME_BYTES,
    LlmAttemptFailure,
    LlmContextSnapshot,
    LlmRequest,
    LlmResponse,
)
from harness.llm import LlmCallError, LlmFailureMetadata
from tests.integration._harness_loop_fakes import (
    FakeShell,
    drain_until_exit,
    make_response,
    run_harness_thread,
)

FILTERED_PROMPT_TOKENS = 211
FILTERED_LATENCY_S = 0.25
SERVICE_UNAVAILABLE_STATUS = 503


@pytest.fixture(autouse=True)
def patch_shell(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    FakeShell.calls = []
    monkeypatch.setattr(harness.shell, "TmuxShell", FakeShell)
    monkeypatch.setenv("TEST_API_KEY", "credential-must-not-appear")
    yield


def test_exact_unicode_context_is_emitted_for_successful_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [make_response(task_complete=True), make_response(task_complete=True)],
        initial_template="任务 🔐 café",
    )

    frames = drain_until_exit(run)

    snapshot = next(frame.data for frame in frames if isinstance(frame.data, LlmContextSnapshot))
    assert [message.model_dump() for message in snapshot.messages] == run.captured_messages[0]
    assert "credential-must-not-appear" not in snapshot.model_dump_json()
    assert snapshot.attempt == 0


def test_retry_emits_one_correlated_context_snapshot_per_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = make_response(task_complete=True)
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [response, response],
        attempts_per_call=[[0, 1], [0]],
    )

    frames = drain_until_exit(run)

    first_requests = [frame.data for frame in frames if isinstance(frame.data, LlmRequest)][:2]
    first_snapshots = [
        frame.data for frame in frames if isinstance(frame.data, LlmContextSnapshot)
    ][:2]
    assert [frame.attempt for frame in first_snapshots] == [0, 1]
    assert {frame.request_id for frame in first_snapshots} == {first_requests[0].request_id}
    assert {frame.turn for frame in first_snapshots} == {0}


def test_no_text_failure_is_emitted_before_harness_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = LlmCallError(
        "provider returned no text: finish_reason=content_filter",
        LlmFailureMetadata(
            category="provider_refusal",
            attempt=0,
            finish_reason="content_filter",
            prompt_tokens=FILTERED_PROMPT_TOKENS,
            completion_tokens=0,
            total_tokens=FILTERED_PROMPT_TOKENS,
            latency_s=FILTERED_LATENCY_S,
        ),
    )
    run = run_harness_thread(tmp_path, monkeypatch, [failure])

    frames = drain_until_exit(run)

    outcome = next(frame.data for frame in frames if isinstance(frame.data, LlmAttemptFailure))
    assert outcome.finish_reason == "content_filter"
    assert (outcome.prompt_tokens, outcome.completion_tokens, outcome.total_tokens) == (
        FILTERED_PROMPT_TOKENS,
        0,
        FILTERED_PROMPT_TOKENS,
    )
    assert outcome.latency_s == FILTERED_LATENCY_S
    assert frames[-2].data == outcome
    assert frames[-1].kind == "harness_exit"


def test_generic_provider_exception_is_emitted_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = LlmCallError(
        "upstream unavailable",
        LlmFailureMetadata(
            category="provider_error",
            attempt=0,
            error_class="RuntimeError",
            status_code=SERVICE_UNAVAILABLE_STATUS,
            latency_s=0.5,
        ),
    )
    run = run_harness_thread(tmp_path, monkeypatch, [failure])

    frames = drain_until_exit(run)

    outcome = next(frame.data for frame in frames if isinstance(frame.data, LlmAttemptFailure))
    assert outcome.error_class == "RuntimeError"
    assert outcome.status_code == SERVICE_UNAVAILABLE_STATUS
    assert "credential-must-not-appear" not in outcome.model_dump_json()


def test_success_keeps_raw_reply_in_llm_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = make_response(task_complete=True)
    run = run_harness_thread(tmp_path, monkeypatch, [response, response])

    frames = drain_until_exit(run)

    llm_response = next(frame.data for frame in frames if isinstance(frame.data, LlmResponse))
    snapshots = [frame.data for frame in frames if isinstance(frame.data, LlmContextSnapshot)]
    assert llm_response.content == response.content
    assert all(response.content not in snapshot.model_dump_json() for snapshot in snapshots)


def test_oversized_context_emits_failure_without_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [make_response(task_complete=True)],
        max_tokens=200_000,
        system_prompt="界" * MAX_FRAME_BYTES,
    )

    frames = drain_until_exit(run)

    failure = next(frame.data for frame in frames if isinstance(frame.data, LlmAttemptFailure))
    assert failure.category == "context_too_large"
    assert failure.attempt == 0
    assert run.captured_messages == []
    assert frames[-1].kind == "harness_exit"
