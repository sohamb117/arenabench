from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from harness.llm_responses import call_copilot_responses

MODEL = "github_copilot/gpt-5.5"
RESOLVED_MODEL = "gpt-5.5"
TOKEN = "copilot-token"
API_BASE = "https://api.enterprise.githubcopilot.com"
CONTENT = "OK"
INPUT_TOKENS = 10
OUTPUT_TOKENS = 5
TOTAL_TOKENS = 15
MAX_OUTPUT_TOKENS = 32
TIMEOUT_S = 60.0
TRUNCATED_OUTPUT_TOKENS = 1024
TRUNCATED_REASONING_TOKENS = 946


@dataclass(frozen=True, slots=True)
class FakeUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float | None = None
    output_tokens_details: FakeOutputDetails | None = None


@dataclass(frozen=True, slots=True)
class FakeOutputDetails:
    reasoning_tokens: int


@dataclass(frozen=True, slots=True)
class FakeIncompleteDetails:
    reason: str


@dataclass(frozen=True, slots=True)
class FakeResponse:
    output_text: str
    usage: FakeUsage
    status: str | None = "completed"
    incomplete_details: FakeIncompleteDetails | None = None


def test_copilot_responses_uses_responses_endpoint_contract() -> None:
    messages = [{"role": "user", "content": "Reply with exactly OK"}]
    calls: list[dict[str, object]] = []

    def caller(**kwargs: object) -> object:
        calls.append(kwargs)
        return FakeResponse(
            output_text=CONTENT,
            usage=FakeUsage(
                input_tokens=INPUT_TOKENS,
                output_tokens=OUTPUT_TOKENS,
                total_tokens=TOTAL_TOKENS,
            ),
        )

    with patch(
        "harness.llm_responses.litellm.get_llm_provider",
        return_value=(RESOLVED_MODEL, "github_copilot", TOKEN, API_BASE),
    ):
        result = call_copilot_responses(
            model=MODEL,
            messages=messages,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            timeout_s=TIMEOUT_S,
            reasoning_effort="medium",
            caller=caller,
        )

    assert result.content == CONTENT
    assert result.input_tokens == INPUT_TOKENS
    assert result.output_tokens == OUTPUT_TOKENS
    assert result.total_tokens == TOTAL_TOKENS
    assert result.status == "completed"
    assert result.incomplete_reason is None
    assert result.reasoning_tokens == 0
    assert calls[0]["model"] == RESOLVED_MODEL
    assert calls[0]["custom_llm_provider"] == "openai"
    assert calls[0]["api_base"] == API_BASE
    assert calls[0]["input"] == messages
    assert calls[0]["reasoning"] == {"effort": "medium"}


def test_copilot_responses_omits_provider_output_limit_when_unset() -> None:
    calls: list[dict[str, object]] = []

    def caller(**kwargs: object) -> object:
        calls.append(kwargs)
        return FakeResponse(
            output_text=CONTENT,
            usage=FakeUsage(
                input_tokens=INPUT_TOKENS,
                output_tokens=OUTPUT_TOKENS,
                total_tokens=TOTAL_TOKENS,
            ),
        )

    with patch(
        "harness.llm_responses.litellm.get_llm_provider",
        return_value=(RESOLVED_MODEL, "github_copilot", TOKEN, API_BASE),
    ):
        call_copilot_responses(
            model=MODEL,
            messages=[{"role": "user", "content": "test"}],
            max_output_tokens=None,
            timeout_s=TIMEOUT_S,
            reasoning_effort="medium",
            caller=caller,
        )

    assert "max_output_tokens" not in calls[0]


def test_copilot_responses_preserves_incomplete_reason_and_reasoning_usage() -> None:
    def caller(**_kwargs: object) -> object:
        return FakeResponse(
            output_text='{"analysis":',
            usage=FakeUsage(
                input_tokens=INPUT_TOKENS,
                output_tokens=TRUNCATED_OUTPUT_TOKENS,
                total_tokens=INPUT_TOKENS + TRUNCATED_OUTPUT_TOKENS,
                output_tokens_details=FakeOutputDetails(
                    reasoning_tokens=TRUNCATED_REASONING_TOKENS
                ),
            ),
            status="incomplete",
            incomplete_details=FakeIncompleteDetails(reason="max_output_tokens"),
        )

    with patch(
        "harness.llm_responses.litellm.get_llm_provider",
        return_value=(RESOLVED_MODEL, "github_copilot", TOKEN, API_BASE),
    ):
        result = call_copilot_responses(
            model=MODEL,
            messages=[{"role": "user", "content": "test"}],
            max_output_tokens=TRUNCATED_OUTPUT_TOKENS,
            timeout_s=TIMEOUT_S,
            reasoning_effort="medium",
            caller=caller,
        )

    assert result.status == "incomplete"
    assert result.incomplete_reason == "max_output_tokens"
    assert result.reasoning_tokens == TRUNCATED_REASONING_TOKENS
