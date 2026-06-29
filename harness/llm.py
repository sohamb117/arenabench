# LiteLLM wrapper adapted from terminal-bench Terminus 2
# (https://github.com/laude-institute/terminal-bench @ 1a6ffa9, Apache-2.0).
# Reimplemented for arenabench; no upstream code copied verbatim.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

import litellm
from litellm import exceptions as litellm_exceptions

from common.clock import elapsed_s, now_monotonic_s
from common.errors import ArenaError

_RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})
_FATAL_STATUS_CODES = frozenset({400, 401, 403, 404, 422})
_SERVER_ERROR_MIN = 500
_SERVER_ERROR_MAX = 599


@dataclass(frozen=True, slots=True)
class LlmCallResult:
    content: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None
    latency_s: float
    error: str | None
    parse_ok: bool = True


class LlmCallError(ArenaError):
    """Raised on FATAL litellm errors that should end the harness."""


class _Message(Protocol):
    content: str


class _Choice(Protocol):
    message: _Message


class _Usage(Protocol):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class _CompletionResponse(Protocol):
    choices: list[_Choice]
    usage: _Usage


class _LiteLlmCompletion(Protocol):
    def __call__(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        timeout: float,
        num_retries: int,
        fallbacks: list[str] | None,
        drop_params: bool,
        max_tokens: int,
        api_key: str | None = None,
    ) -> object: ...


def call(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout_s: float,
    num_retries: int,
    fallbacks: list[str] | None,
    api_key: str | None = None,
    mock_response: str | None = None,
    on_attempt: Callable[[int], None] | None = None,
) -> LlmCallResult:
    """
    Single LiteLLM completion call. Times out after timeout_s; retries
    transient errors per Terminus 2 / LiteLLM defaults (litellm.num_retries).
    Raises LlmCallError on FATAL errors. Returns LlmCallResult on success.

    When mock_response is set, returns it directly without hitting any real
    LLM API. Used by deterministic e2e fixtures (plan §9 S15/S17 fake-LLM).

    `on_attempt(attempt_index)` fires immediately BEFORE each network attempt
    (attempt_index starts at 0). Plan §9 S16 binary observable: an external
    caller (harness loop) emits one `llm_request` frame per attempt so retry
    count is observable in api.jsonl without parsing harness logs.
    """
    started = now_monotonic_s()
    if mock_response is not None:
        if on_attempt is not None:
            on_attempt(0)
        return LlmCallResult(
            content=mock_response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            cost_usd=0.0,
            latency_s=elapsed_s(started),
            error=None,
        )
    completion = cast(_LiteLlmCompletion, litellm.completion)
    last_retryable: Exception | None = None
    # Plan §9 S16: pass num_retries=0 to LiteLLM so its internal retry loop is
    # disabled — our outer loop is the SOLE retry mechanism, and every attempt
    # fires on_attempt() so api.jsonl gets one llm_request frame per attempt.
    # LiteLLM-internal retries would collapse N attempts into one observable.
    for attempt in range(max(1, num_retries)):
        if on_attempt is not None:
            on_attempt(attempt)
        try:
            if api_key is None:
                raw_response = completion(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    timeout=timeout_s,
                    num_retries=0,
                    fallbacks=fallbacks or None,
                    drop_params=True,
                    max_tokens=max_tokens,
                )
            else:
                raw_response = completion(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    timeout=timeout_s,
                    num_retries=0,
                    fallbacks=fallbacks or None,
                    drop_params=True,
                    max_tokens=max_tokens,
                    api_key=api_key,
                )
            response = cast(_CompletionResponse, raw_response)
            return LlmCallResult(
                content=response.choices[0].message.content,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                cost_usd=_completion_cost(response),
                latency_s=elapsed_s(started),
                error=None,
            )
        except Exception as exc:
            if _is_fatal(exc):
                raise LlmCallError(str(exc)) from exc
            if not _is_retryable(exc):
                raise
            last_retryable = exc

    return LlmCallResult(
        content="",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost_usd=None,
        latency_s=elapsed_s(started),
        error=str(last_retryable) if last_retryable is not None else "llm call failed",
        parse_ok=False,
    )


def _completion_cost(response: _CompletionResponse) -> float | None:
    try:
        raw_cost = litellm.completion_cost(completion_response=response)
    except Exception:
        return None
    return float(raw_cost)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, litellm_exceptions.APIConnectionError):
        return True
    status_code = _status_code(exc)
    return (
        status_code in _RETRYABLE_STATUS_CODES
        or _SERVER_ERROR_MIN <= status_code <= _SERVER_ERROR_MAX
    )


def _is_fatal(exc: Exception) -> bool:
    if isinstance(exc, litellm_exceptions.ContextWindowExceededError):
        return True
    return _status_code(exc) in _FATAL_STATUS_CODES


def _status_code(exc: Exception) -> int:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    direct_status = getattr(exc, "status_code", None)
    if isinstance(direct_status, int):
        return direct_status
    return 0
