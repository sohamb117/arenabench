"""Plan §9 S16 observability: llm.call() invokes on_attempt(i) once per
network attempt so the harness can emit one `llm_request` envelope per
retry to `api.jsonl`. Also locks num_retries semantics (retries AFTER
initial, per LiteLLM convention) — total attempts == num_retries + 1.
"""

from __future__ import annotations

from unittest.mock import patch

from litellm import exceptions as litellm_exceptions

from harness.llm import call
from tests.unit._llm_fixtures import (
    CONTENT,
    COST_USD,
    FALLBACK_MODEL,
    MAX_TOKENS,
    MESSAGES,
    MODEL,
    NUM_RETRIES,
    TEMPERATURE,
    TIMEOUT_S,
    make_response,
)

_TWO_ATTEMPTS = 2


def test_call_fires_on_attempt_once_per_retry_for_429_then_success() -> None:
    rate_limited = litellm_exceptions.RateLimitError("429", llm_provider="openai", model=MODEL)
    attempts: list[int] = []

    with (
        patch(
            "harness.llm.litellm.completion",
            side_effect=[rate_limited, make_response()],
        ),
        patch("harness.llm.litellm.completion_cost", return_value=COST_USD),
    ):
        result = call(
            model=MODEL,
            messages=MESSAGES,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            timeout_s=TIMEOUT_S,
            num_retries=NUM_RETRIES,
            fallbacks=[FALLBACK_MODEL],
            on_attempt=attempts.append,
        )

    assert attempts == [0, 1]
    assert result.content == CONTENT
    assert result.error is None


def test_call_fires_on_attempt_once_for_mock_response() -> None:
    attempts: list[int] = []

    result = call(
        model=MODEL,
        messages=MESSAGES,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        timeout_s=TIMEOUT_S,
        num_retries=NUM_RETRIES,
        fallbacks=None,
        mock_response="mock content",
        on_attempt=attempts.append,
    )

    assert attempts == [0]
    assert result.content == "mock content"


def test_call_with_num_retries_zero_makes_exactly_one_attempt() -> None:
    """num_retries=0 means zero retries AFTER initial — total 1 attempt."""
    attempts: list[int] = []

    with (
        patch("harness.llm.litellm.completion", return_value=make_response()) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=COST_USD),
    ):
        result = call(
            model=MODEL,
            messages=MESSAGES,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            timeout_s=TIMEOUT_S,
            num_retries=0,
            fallbacks=None,
            on_attempt=attempts.append,
        )

    assert attempts == [0]
    assert completion.call_count == 1
    assert result.error is None


def test_call_with_num_retries_one_makes_two_attempts_on_retryable() -> None:
    """num_retries=1 means one retry AFTER initial — total 2 attempts."""
    rate_limited = litellm_exceptions.RateLimitError("429", llm_provider="openai", model=MODEL)
    attempts: list[int] = []

    with (
        patch(
            "harness.llm.litellm.completion",
            side_effect=[rate_limited, make_response()],
        ) as completion,
        patch("harness.llm.litellm.completion_cost", return_value=COST_USD),
    ):
        result = call(
            model=MODEL,
            messages=MESSAGES,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            timeout_s=TIMEOUT_S,
            num_retries=1,
            fallbacks=None,
            on_attempt=attempts.append,
        )

    assert attempts == [0, 1]
    assert completion.call_count == _TWO_ATTEMPTS
    assert result.content == CONTENT
    assert result.error is None


def test_call_with_num_retries_one_exhausts_after_two_failures() -> None:
    """num_retries=1: both attempts fail → result.error set, attempts=[0,1]."""
    rate_limited = litellm_exceptions.RateLimitError("429", llm_provider="openai", model=MODEL)
    attempts: list[int] = []

    with patch(
        "harness.llm.litellm.completion",
        side_effect=[rate_limited, rate_limited],
    ) as completion:
        result = call(
            model=MODEL,
            messages=MESSAGES,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            timeout_s=TIMEOUT_S,
            num_retries=1,
            fallbacks=None,
            on_attempt=attempts.append,
        )

    assert attempts == [0, 1]
    assert completion.call_count == _TWO_ATTEMPTS
    assert result.error is not None
    assert "429" in result.error
