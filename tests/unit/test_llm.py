from __future__ import annotations

from unittest.mock import patch

import pytest
from litellm import exceptions as litellm_exceptions

from harness.llm import LlmCallError, LlmCallResult, call
from harness.llm_responses import ResponsesResult
from tests.unit._llm_fixtures import (
    API_KEY,
    COMPLETION_TOKENS,
    CONTENT,
    COST_USD,
    FALLBACK_MODEL,
    MAX_TOKENS,
    MESSAGES,
    MODEL,
    NUM_RETRIES,
    PROMPT_TOKENS,
    TEMPERATURE,
    TIMEOUT_S,
    TOTAL_TOKENS,
    make_response,
)

FILTERED_PROMPT_TOKENS = 211
TRUNCATED_OUTPUT_TOKENS = 1024
TRUNCATED_REASONING_TOKENS = 946


def _call(api_key: str | None = None) -> LlmCallResult:
    return call(
        model=MODEL,
        messages=MESSAGES,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        timeout_s=TIMEOUT_S,
        num_retries=NUM_RETRIES,
        fallbacks=[FALLBACK_MODEL],
        api_key=api_key,
    )


def test_call_returns_usage_cost_and_content_when_litellm_succeeds() -> None:
    resp = make_response()
    with (
        patch("harness.llm.litellm.completion", return_value=resp) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=COST_USD),
    ):
        result = _call()

    assert result == LlmCallResult(
        content=CONTENT,
        prompt_tokens=PROMPT_TOKENS,
        completion_tokens=COMPLETION_TOKENS,
        total_tokens=TOTAL_TOKENS,
        cost_usd=COST_USD,
        latency_s=result.latency_s,
        error=None,
        attempt=0,
        parse_ok=True,
    )
    completion.assert_called_once_with(
        model=MODEL,
        messages=MESSAGES,
        temperature=TEMPERATURE,
        timeout=TIMEOUT_S,
        num_retries=0,
        fallbacks=[FALLBACK_MODEL],
        drop_params=True,
        max_tokens=MAX_TOKENS,
        reasoning_effort=None,
        api_key=None,
    )


def test_call_retries_retryable_error_then_returns_success() -> None:
    retryable = litellm_exceptions.APIConnectionError(
        "temporary connection failure",
        llm_provider="openai",
        model=MODEL,
    )
    with (
        patch(
            "harness.llm.litellm.completion",
            side_effect=[retryable, make_response()],
        ) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=COST_USD),
    ):
        result = _call()

    assert result.content == CONTENT
    assert result.error is None
    assert completion.call_count == NUM_RETRIES


def test_call_sets_cost_to_none_when_cost_capture_fails() -> None:
    with (
        patch("harness.llm.litellm.completion", return_value=make_response()),
        patch("harness.llm.litellm.completion_cost", side_effect=RuntimeError("no price")),
    ):
        result = _call()

    assert result.cost_usd is None


def test_call_raises_llm_call_error_for_authentication_error() -> None:
    fatal = litellm_exceptions.AuthenticationError(
        "bad key",
        llm_provider="openai",
        model=MODEL,
    )
    with (
        patch("harness.llm.litellm.completion", side_effect=fatal),
        pytest.raises(LlmCallError) as exc,
    ):
        _call()

    assert "bad key" in str(exc.value)


def test_call_raises_llm_call_error_for_context_window_error() -> None:
    fatal = litellm_exceptions.ContextWindowExceededError(
        "too many tokens",
        llm_provider="openai",
        model=MODEL,
    )
    with (
        patch("harness.llm.litellm.completion", side_effect=fatal),
        pytest.raises(LlmCallError) as exc,
    ):
        _call()

    assert "too many tokens" in str(exc.value)


def test_call_reports_nonnegative_latency_within_timeout() -> None:
    with (
        patch("harness.llm.litellm.completion", return_value=make_response()),
        patch("harness.llm.litellm.completion_cost", return_value=COST_USD),
    ):
        result = _call()

    assert 0.0 <= result.latency_s <= TIMEOUT_S


def test_call_passes_drop_params_true() -> None:
    with (
        patch("harness.llm.litellm.completion", return_value=make_response()) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=COST_USD),
    ):
        _call()

    completion.assert_called_once()
    assert completion.call_args.kwargs["drop_params"] is True


def test_call_passes_api_key_when_provided() -> None:
    with (
        patch("harness.llm.litellm.completion", return_value=make_response()) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=COST_USD),
    ):
        _call(api_key=API_KEY)

    completion.assert_called_once()
    assert completion.call_args.kwargs["api_key"] == API_KEY


def test_call_passes_reasoning_effort_when_provided() -> None:
    with (
        patch("harness.llm.litellm.completion", return_value=make_response()) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=COST_USD),
    ):
        call(
            model=MODEL,
            messages=MESSAGES,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            timeout_s=TIMEOUT_S,
            num_retries=NUM_RETRIES,
            fallbacks=[FALLBACK_MODEL],
            reasoning_effort="medium",
        )

    completion.assert_called_once()
    assert completion.call_args.kwargs["reasoning_effort"] == "medium"


def test_call_normalizes_responses_result() -> None:
    response = ResponsesResult(
        content=CONTENT,
        input_tokens=PROMPT_TOKENS,
        output_tokens=COMPLETION_TOKENS,
        total_tokens=TOTAL_TOKENS,
        cost_usd=COST_USD,
        status="completed",
        incomplete_reason=None,
        reasoning_tokens=0,
    )
    with patch("harness.llm.call_copilot_responses", return_value=response) as responses:
        result = call(
            model="github_copilot/gpt-5.5",
            messages=MESSAGES,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            timeout_s=TIMEOUT_S,
            num_retries=NUM_RETRIES,
            fallbacks=None,
            reasoning_effort="medium",
            api_mode="responses",
        )

    assert result.content == CONTENT
    assert result.prompt_tokens == PROMPT_TOKENS
    assert result.completion_tokens == COMPLETION_TOKENS
    assert result.total_tokens == TOTAL_TOKENS
    assert result.cost_usd == COST_USD
    responses.assert_called_once_with(
        model="github_copilot/gpt-5.5",
        messages=MESSAGES,
        max_output_tokens=MAX_TOKENS,
        timeout_s=TIMEOUT_S,
        reasoning_effort="medium",
    )


def test_call_reports_incomplete_responses_metadata() -> None:
    response = ResponsesResult(
        content='{"analysis":',
        input_tokens=PROMPT_TOKENS,
        output_tokens=TRUNCATED_OUTPUT_TOKENS,
        total_tokens=PROMPT_TOKENS + TRUNCATED_OUTPUT_TOKENS,
        cost_usd=None,
        status="incomplete",
        incomplete_reason="max_output_tokens",
        reasoning_tokens=TRUNCATED_REASONING_TOKENS,
    )
    with (
        patch("harness.llm.call_copilot_responses", return_value=response),
        pytest.raises(LlmCallError, match=f"reasoning_tokens={TRUNCATED_REASONING_TOKENS}") as exc,
    ):
        call(
            model="github_copilot/gpt-5.5",
            messages=MESSAGES,
            temperature=TEMPERATURE,
            max_tokens=TRUNCATED_OUTPUT_TOKENS,
            timeout_s=TIMEOUT_S,
            num_retries=0,
            fallbacks=None,
            reasoning_effort="medium",
            api_mode="responses",
        )

    assert exc.value.finish_reason == "max_output_tokens"
    assert exc.value.completion_tokens == TRUNCATED_OUTPUT_TOKENS


def test_call_raises_llm_call_error_for_bad_request_error() -> None:
    fatal = litellm_exceptions.BadRequestError(
        "bad request",
        llm_provider="openai",
        model=MODEL,
    )
    with (
        patch("harness.llm.litellm.completion", side_effect=fatal),
        pytest.raises(LlmCallError) as exc,
    ):
        _call()

    assert "bad request" in str(exc.value)


def test_call_raises_llm_call_error_when_provider_returns_no_text() -> None:
    response = make_response(
        content=None,
        finish_reason="content_filter",
        prompt_tokens=FILTERED_PROMPT_TOKENS,
        completion_tokens=0,
        total_tokens=FILTERED_PROMPT_TOKENS,
    )
    with (
        patch("harness.llm.litellm.completion", return_value=response),
        pytest.raises(LlmCallError, match="finish_reason=content_filter") as exc,
    ):
        _call()

    assert exc.value.category == "provider_refusal"
    assert exc.value.finish_reason == "content_filter"
    assert exc.value.prompt_tokens == FILTERED_PROMPT_TOKENS
    assert exc.value.completion_tokens == 0
    assert exc.value.total_tokens == FILTERED_PROMPT_TOKENS
    assert exc.value.attempt == 0
    assert exc.value.latency_s is not None


def test_call_wraps_generic_provider_exception_with_safe_metadata() -> None:
    provider_error = RuntimeError("upstream unavailable")

    with (
        patch("harness.llm.litellm.completion", side_effect=provider_error),
        pytest.raises(LlmCallError, match="upstream unavailable") as exc,
    ):
        _call()

    assert exc.value.category == "provider_error"
    assert exc.value.error_class == "RuntimeError"
    assert exc.value.status_code is None
    assert exc.value.attempt == 0
    assert exc.value.error_text == "upstream unavailable"


def test_call_redacts_credentials_from_provider_exception_text() -> None:
    provider_error = RuntimeError(
        "Authorization: Bearer credential-must-not-appear api_key=other-secret"
    )

    with (
        patch("harness.llm.litellm.completion", side_effect=provider_error),
        pytest.raises(LlmCallError) as exc,
    ):
        _call()

    assert "credential-must-not-appear" not in exc.value.error_text
    assert "other-secret" not in exc.value.error_text
    assert "[REDACTED]" in exc.value.error_text
