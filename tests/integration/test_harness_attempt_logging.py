from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

import harness.shell
from common.context_protocol import decode_context_chunks
from common.protocol import (
    MAX_FRAME_BYTES,
    LlmAttemptFailure,
    LlmContextChunk,
    LlmRequest,
    LlmResponse,
    serialize_envelope,
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
EXPECTED_PROVIDER_CALLS = 2


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

    chunks = [
        frame.data
        for frame in frames
        if isinstance(frame.data, LlmContextChunk) and frame.data.turn == 0
    ]
    assert decode_context_chunks(chunks) == run.captured_messages[0]
    assert "credential-must-not-appear" not in "".join(chunk.payload_b64 for chunk in chunks)
    assert chunks[0].attempt == 0


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
    first_chunks = [frame.data for frame in frames if isinstance(frame.data, LlmContextChunk)][:2]
    assert [frame.attempt for frame in first_chunks] == [0, 1]
    assert {frame.request_id for frame in first_chunks} == {first_requests[0].request_id}
    assert {frame.turn for frame in first_chunks} == {0}


def test_retryable_failure_is_emitted_before_next_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retry_failure = LlmCallError(
        "temporary token=sk-proj-retry-secret",
        LlmFailureMetadata(
            category="provider_error",
            attempt=0,
            error_class="APIConnectionError",
            status_code=SERVICE_UNAVAILABLE_STATUS,
            latency_s=0.2,
        ),
    )
    response = make_response(task_complete=True)
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [response, response],
        attempts_per_call=[[0, 1], [0]],
        failure_plans=[[retry_failure], []],
    )

    frames = drain_until_exit(run)

    first_retry = next(
        index
        for index, frame in enumerate(frames)
        if isinstance(frame.data, LlmRequest) and frame.data.attempt == 1
    )
    failure_index = next(
        index
        for index, frame in enumerate(frames)
        if isinstance(frame.data, LlmAttemptFailure) and frame.data.attempt == 0
    )
    failure = frames[failure_index].data
    assert isinstance(failure, LlmAttemptFailure)
    assert failure.category == "provider_error"
    assert failure.error_class == "APIConnectionError"
    assert "sk-proj-retry-secret" not in failure.error_text
    assert failure_index < first_retry


def test_context_logging_failure_does_not_skip_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_context(*_args: object) -> tuple[()]:
        raise ValueError("api_key=sk-context-secret")

    monkeypatch.setattr("harness.attempt_logging.context_frames", fail_context)
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [make_response(task_complete=True), make_response(task_complete=True)],
    )

    frames = drain_until_exit(run)

    failures = [
        frame.data
        for frame in frames
        if isinstance(frame.data, LlmAttemptFailure)
        and frame.data.category == "context_logging_error"
    ]
    assert len(run.captured_messages) == EXPECTED_PROVIDER_CALLS
    assert len(failures) == EXPECTED_PROVIDER_CALLS
    assert all("sk-context-secret" not in failure.error_text for failure in failures)


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
    snapshots = [frame.data for frame in frames if isinstance(frame.data, LlmContextChunk)]
    assert llm_response.content == response.content
    assert all(response.content not in snapshot.model_dump_json() for snapshot in snapshots)


def test_realistic_large_context_is_chunked_and_provider_still_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    turns = "".join(f"turn {index}: 秘密 café 🔐\n" + "x" * 4_096 for index in range(20))
    run = run_harness_thread(
        tmp_path,
        monkeypatch,
        [make_response(task_complete=True), make_response(task_complete=True)],
        max_tokens=200_000,
        system_prompt=turns,
    )

    frames = drain_until_exit(run)

    chunks = [frame.data for frame in frames if isinstance(frame.data, LlmContextChunk)]
    first_attempt = [chunk for chunk in chunks if chunk.turn == 0 and chunk.attempt == 0]
    assert len(first_attempt) > 1
    assert decode_context_chunks(first_attempt) == run.captured_messages[0]
    assert len(run.captured_messages) == EXPECTED_PROVIDER_CALLS
    assert all(len(serialize_envelope(frame).encode()) <= MAX_FRAME_BYTES for frame in frames)
