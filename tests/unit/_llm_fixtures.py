"""Shared LiteLLM fakes for test_llm.py + test_llm_on_attempt.py.

Mirrors tests/integration/_harness_loop_fakes.py: module name underscored to
signal test-internal usage, exported symbols themselves are public so
basedpyright's reportPrivateUsage does not fire when cross-module imports
happen.
"""

from __future__ import annotations

from dataclasses import dataclass

MODEL = "openai/gpt-4o"
FALLBACK_MODEL = "anthropic/claude-3-5-sonnet"
API_KEY = "test-key"
CONTENT = "run whoami"
PROMPT_TOKENS = 11
COMPLETION_TOKENS = 7
TOTAL_TOKENS = 18
COST_USD = 0.0123
TEMPERATURE = 0.2
MAX_TOKENS = 256
TIMEOUT_S = 10.0
NUM_RETRIES = 2
AUTH_STATUS = 401

MESSAGES: list[dict[str, str]] = [{"role": "user", "content": "hello"}]


@dataclass(frozen=True, slots=True)
class FakeMessage:
    content: str | None


@dataclass(frozen=True, slots=True)
class FakeChoice:
    message: FakeMessage
    finish_reason: str | None = "stop"


@dataclass(frozen=True, slots=True)
class FakeUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class FakeResponse:
    choices: list[FakeChoice]
    usage: FakeUsage


def make_response(
    content: str | None = CONTENT, finish_reason: str | None = "stop"
) -> FakeResponse:
    return FakeResponse(
        choices=[FakeChoice(message=FakeMessage(content=content), finish_reason=finish_reason)],
        usage=FakeUsage(
            prompt_tokens=PROMPT_TOKENS,
            completion_tokens=COMPLETION_TOKENS,
            total_tokens=TOTAL_TOKENS,
        ),
    )
