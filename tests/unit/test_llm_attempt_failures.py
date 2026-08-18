from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

from litellm import exceptions as litellm_exceptions

from harness._loop_helpers import build_llm_response
from harness.llm import LlmCallError, LlmCallResult, call
from tests.unit._llm_fixtures import (
    FALLBACK_MODEL,
    MAX_TOKENS,
    MESSAGES,
    MODEL,
    TEMPERATURE,
    TIMEOUT_S,
    make_response,
)

MAX_ERROR_TEXT_CHARS = 1_024


def _call(on_failure: Callable[[LlmCallError], None]) -> LlmCallResult:
    return call(
        model=MODEL,
        messages=MESSAGES,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        timeout_s=TIMEOUT_S,
        num_retries=1,
        fallbacks=[FALLBACK_MODEL],
        on_failure=on_failure,
    )


def test_retryable_attempt_failure_is_reported_before_retry() -> None:
    events: list[str] = []
    retryable = litellm_exceptions.APIConnectionError(
        "temporary api_key=sk-retry-secret", llm_provider="openai", model=MODEL
    )

    def record(error: LlmCallError) -> None:
        events.append(f"failure:{error.attempt}:{error.category}:{error.error_text}")

    call_count = 0

    def complete(**_: object) -> object:
        nonlocal call_count
        events.append("call")
        call_count += 1
        if call_count == 1:
            raise retryable
        return make_response()

    with patch(
        "harness.llm.litellm.completion",
        side_effect=complete,
    ):
        result = _call(record)

    assert result.content == make_response().choices[0].message.content
    assert events[0] == "call"
    assert events[1].startswith("failure:0:provider_error:")
    assert "sk-retry-secret" not in events[1]
    assert events[2] == "call"


def test_retry_exhaustion_returns_bounded_redacted_error_and_reports_each_attempt() -> None:
    failures: list[LlmCallError] = []
    secret = "github_pat_abcdefghijklmnopqrstuvwxyz"
    retryable = litellm_exceptions.APIConnectionError(
        f"token={secret} " + "x" * 4_000, llm_provider="openai", model=MODEL
    )

    with patch("harness.llm.litellm.completion", side_effect=retryable):
        result = _call(failures.append)

    assert [failure.attempt for failure in failures] == [0, 1]
    assert all(failure.category == "provider_error" for failure in failures)
    assert secret not in (result.error or "")
    assert result.error is not None
    assert len(result.error) <= MAX_ERROR_TEXT_CHARS


def test_llm_response_error_is_redacted_and_bounded() -> None:
    secret = "sk-proj-response-secret"
    result = LlmCallResult(
        content="",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost_usd=None,
        latency_s=0.2,
        error=f"api_key={secret} " + "x" * 4_000,
    )

    response = build_llm_response(result, "json", 2, "req", False, result.error)

    assert response.error is not None
    assert secret not in response.error
    assert len(response.error) <= MAX_ERROR_TEXT_CHARS
