from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

import pytest
from litellm import exceptions as litellm_exceptions

from harness.llm import LlmCallError, LlmCallResult, call

_MODEL = "openai/gpt-4o"
_FALLBACK_MODEL = "anthropic/claude-3-5-sonnet"
_API_KEY = "test-key"
_CONTENT = "run whoami"
_PROMPT_TOKENS = 11
_COMPLETION_TOKENS = 7
_TOTAL_TOKENS = 18
_COST_USD = 0.0123
_TEMPERATURE = 0.2
_MAX_TOKENS = 256
_TIMEOUT_S = 10.0
_NUM_RETRIES = 2
_AUTH_STATUS = 401

_MESSAGES: list[dict[str, str]] = [{"role": "user", "content": "hello"}]


@dataclass(frozen=True, slots=True)
class _FakeMessage:
    content: str


@dataclass(frozen=True, slots=True)
class _FakeChoice:
    message: _FakeMessage


@dataclass(frozen=True, slots=True)
class _FakeUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class _FakeResponse:
    choices: list[_FakeChoice]
    usage: _FakeUsage


def _response(content: str = _CONTENT) -> _FakeResponse:
    return _FakeResponse(
        choices=[_FakeChoice(message=_FakeMessage(content=content))],
        usage=_FakeUsage(
            prompt_tokens=_PROMPT_TOKENS,
            completion_tokens=_COMPLETION_TOKENS,
            total_tokens=_TOTAL_TOKENS,
        ),
    )


def _call(api_key: str | None = None) -> LlmCallResult:
    return call(
        model=_MODEL,
        messages=_MESSAGES,
        temperature=_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
        timeout_s=_TIMEOUT_S,
        num_retries=_NUM_RETRIES,
        fallbacks=[_FALLBACK_MODEL],
        api_key=api_key,
    )


def test_call_returns_usage_cost_and_content_when_litellm_succeeds() -> None:
    resp = _response()
    with (
        patch("harness.llm.litellm.completion", return_value=resp) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=_COST_USD),
    ):
        result = _call()

    assert result == LlmCallResult(
        content=_CONTENT,
        prompt_tokens=_PROMPT_TOKENS,
        completion_tokens=_COMPLETION_TOKENS,
        total_tokens=_TOTAL_TOKENS,
        cost_usd=_COST_USD,
        latency_s=result.latency_s,
        error=None,
        parse_ok=True,
    )
    completion.assert_called_once_with(
        model=_MODEL,
        messages=_MESSAGES,
        temperature=_TEMPERATURE,
        timeout=_TIMEOUT_S,
        num_retries=_NUM_RETRIES,
        fallbacks=[_FALLBACK_MODEL],
        drop_params=True,
        max_tokens=_MAX_TOKENS,
    )


def test_call_retries_retryable_error_then_returns_success() -> None:
    retryable = litellm_exceptions.APIConnectionError(
        "temporary connection failure",
        llm_provider="openai",
        model=_MODEL,
    )
    with (
        patch("harness.llm.litellm.completion", side_effect=[retryable, _response()]) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=_COST_USD),
    ):
        result = _call()

    assert result.content == _CONTENT
    assert result.error is None
    assert completion.call_count == _NUM_RETRIES


def test_call_sets_cost_to_none_when_cost_capture_fails() -> None:
    with (
        patch("harness.llm.litellm.completion", return_value=_response()),
        patch("harness.llm.litellm.completion_cost", side_effect=RuntimeError("no price")),
    ):
        result = _call()

    assert result.cost_usd is None


def test_call_raises_llm_call_error_for_authentication_error() -> None:
    fatal = litellm_exceptions.AuthenticationError(
        "bad key",
        llm_provider="openai",
        model=_MODEL,
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
        model=_MODEL,
    )
    with (
        patch("harness.llm.litellm.completion", side_effect=fatal),
        pytest.raises(LlmCallError) as exc,
    ):
        _call()

    assert "too many tokens" in str(exc.value)


def test_call_reports_nonnegative_latency_within_timeout() -> None:
    with (
        patch("harness.llm.litellm.completion", return_value=_response()),
        patch("harness.llm.litellm.completion_cost", return_value=_COST_USD),
    ):
        result = _call()

    assert 0.0 <= result.latency_s <= _TIMEOUT_S


def test_call_passes_drop_params_true() -> None:
    with (
        patch("harness.llm.litellm.completion", return_value=_response()) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=_COST_USD),
    ):
        _call()

    completion.assert_called_once()
    assert completion.call_args.kwargs["drop_params"] is True


def test_call_passes_api_key_when_provided() -> None:
    with (
        patch("harness.llm.litellm.completion", return_value=_response()) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=_COST_USD),
    ):
        _call(api_key=_API_KEY)

    completion.assert_called_once()
    assert completion.call_args.kwargs["api_key"] == _API_KEY


def test_call_raises_llm_call_error_for_bad_request_error() -> None:
    fatal = litellm_exceptions.BadRequestError(
        "bad request",
        llm_provider="openai",
        model=_MODEL,
    )
    with (
        patch("harness.llm.litellm.completion", side_effect=fatal),
        pytest.raises(LlmCallError) as exc,
    ):
        _call()

    assert "bad request" in str(exc.value)
