# LiteLLM wrapper adapted from terminal-bench Terminus 2
# (https://github.com/laude-institute/terminal-bench @ 1a6ffa9, Apache-2.0).
# Reimplemented for arenabench; no upstream code copied verbatim.

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, cast

import litellm
from litellm import exceptions as litellm_exceptions

from common.clock import elapsed_s, now_monotonic_s
from common.errors import ArenaError
from harness.llm_cost import completion_cost
from harness.llm_responses import call_copilot_responses
from harness.llm_types import CompletionResponse

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
    attempt: int = 0
    parse_ok: bool = True


type LlmFailureCategory = Literal[
    "context_logging_error",
    "context_too_large",
    "provider_error",
    "provider_refusal",
    "reservation_error",
]
type ApiMode = Literal["chat_completions", "responses"]


@dataclass(frozen=True, slots=True)
class LlmFailureMetadata:
    category: LlmFailureCategory
    attempt: int
    finish_reason: str | None = None
    error_class: str | None = None
    status_code: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_s: float | None = None


class LlmCallError(ArenaError):
    """Raised on FATAL litellm errors that should end the harness."""

    def __init__(self, message: str, metadata: LlmFailureMetadata | None = None) -> None:
        super().__init__(message)
        self.error_text = message
        self.metadata = metadata

    @property
    def category(self) -> LlmFailureCategory | None:
        return self.metadata.category if self.metadata is not None else None

    @property
    def attempt(self) -> int | None:
        return self.metadata.attempt if self.metadata is not None else None

    @property
    def finish_reason(self) -> str | None:
        return self.metadata.finish_reason if self.metadata is not None else None

    @property
    def error_class(self) -> str | None:
        return self.metadata.error_class if self.metadata is not None else None

    @property
    def status_code(self) -> int | None:
        return self.metadata.status_code if self.metadata is not None else None

    @property
    def prompt_tokens(self) -> int | None:
        return self.metadata.prompt_tokens if self.metadata is not None else None

    @property
    def completion_tokens(self) -> int | None:
        return self.metadata.completion_tokens if self.metadata is not None else None

    @property
    def total_tokens(self) -> int | None:
        return self.metadata.total_tokens if self.metadata is not None else None

    @property
    def latency_s(self) -> float | None:
        return self.metadata.latency_s if self.metadata is not None else None


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
        reasoning_effort: str | None = None,
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
    reasoning_effort: str | None = None,
    api_mode: ApiMode = "chat_completions",
    api_key: str | None = None,
    mock_response: str | None = None,
    on_attempt: Callable[[int], None] | None = None,
    on_failure: Callable[[LlmCallError], None] | None = None,
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
            attempt=0,
        )
    completion = cast(_LiteLlmCompletion, litellm.completion)
    last_retryable: Exception | None = None
    # Plan §9 S16: pass num_retries=0 to LiteLLM so its internal retry loop is
    # disabled — our outer loop is the SOLE retry mechanism, and every attempt
    # fires on_attempt() so api.jsonl gets one llm_request frame per attempt.
    # LiteLLM-internal retries would collapse N attempts into one observable.
    total_attempts = num_retries + 1
    for attempt in range(total_attempts):
        if on_attempt is not None:
            on_attempt(attempt)
        attempt_started = now_monotonic_s()
        try:
            if api_mode == "responses":
                return _responses_call(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    timeout_s=timeout_s,
                    reasoning_effort=reasoning_effort,
                    attempt=attempt,
                    started=started,
                )
            raw_response = completion(
                model=model,
                messages=messages,
                temperature=temperature,
                timeout=timeout_s,
                num_retries=0,
                fallbacks=fallbacks or None,
                drop_params=True,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                api_key=api_key,
            )
            response = cast(CompletionResponse, raw_response)
            content = response.choices[0].message.content
            if content is None:
                finish_reason = response.choices[0].finish_reason or "unknown"
                raise LlmCallError(
                    f"provider returned no text: finish_reason={finish_reason}",
                    LlmFailureMetadata(
                        category="provider_refusal",
                        attempt=attempt,
                        finish_reason=finish_reason,
                        prompt_tokens=response.usage.prompt_tokens,
                        completion_tokens=response.usage.completion_tokens,
                        total_tokens=response.usage.total_tokens,
                        latency_s=elapsed_s(started),
                    ),
                )
            return LlmCallResult(
                content=content,
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
                cost_usd=completion_cost(response),
                latency_s=elapsed_s(started),
                error=None,
                attempt=attempt,
            )
        except Exception as exc:
            if isinstance(exc, LlmCallError):
                raise
            if _is_fatal(exc):
                raise _provider_error(exc, attempt, attempt_started) from exc
            if not _is_retryable(exc):
                raise _provider_error(exc, attempt, attempt_started) from exc
            last_retryable = exc
            failure = _provider_error(exc, attempt, attempt_started)
            if on_failure is not None:
                on_failure(failure)

    return LlmCallResult(
        content="",
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost_usd=None,
        latency_s=elapsed_s(started),
        error=(
            _provider_error(last_retryable, total_attempts - 1, started).error_text
            if last_retryable is not None
            else "llm call failed"
        ),
        attempt=total_attempts - 1,
        parse_ok=False,
    )


def _responses_call(
    *,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    timeout_s: float,
    reasoning_effort: str | None,
    attempt: int,
    started: float,
) -> LlmCallResult:
    if not model.startswith("github_copilot/"):
        raise LlmCallError(
            "responses mode currently requires a GitHub Copilot model",
            LlmFailureMetadata(category="provider_error", attempt=attempt),
        )
    response = call_copilot_responses(
        model=model,
        messages=messages,
        max_output_tokens=max_tokens,
        timeout_s=timeout_s,
        reasoning_effort=reasoning_effort or "none",
    )
    if not response.content:
        raise LlmCallError(
            "provider returned no text: finish_reason=unknown",
            LlmFailureMetadata(
                category="provider_refusal",
                attempt=attempt,
                finish_reason="unknown",
                prompt_tokens=response.input_tokens,
                completion_tokens=response.output_tokens,
                total_tokens=response.total_tokens,
                latency_s=elapsed_s(started),
            ),
        )
    return LlmCallResult(
        content=response.content,
        prompt_tokens=response.input_tokens,
        completion_tokens=response.output_tokens,
        total_tokens=response.total_tokens,
        cost_usd=response.cost_usd,
        latency_s=elapsed_s(started),
        error=None,
        attempt=attempt,
    )


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


def _provider_error(exc: Exception, attempt: int, started: float) -> LlmCallError:
    from harness.attempt_logging import safe_error_text  # noqa: PLC0415

    status_code = _status_code(exc)
    return LlmCallError(
        safe_error_text(str(exc)),
        LlmFailureMetadata(
            category="provider_error",
            attempt=attempt,
            error_class=type(exc).__name__,
            status_code=status_code or None,
            latency_s=elapsed_s(started),
        ),
    )
