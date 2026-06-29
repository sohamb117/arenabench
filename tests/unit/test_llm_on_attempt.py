"""Plan §9 S16 observability: llm.call() invokes on_attempt(i) once per
network attempt so the harness can emit one `llm_request` envelope per
retry to `api.jsonl`.
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
