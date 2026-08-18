from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from common import protocol as proto
from common.context_protocol import context_chunks
from common.errors import TransportError
from harness import llm

MAX_ERROR_TEXT_CHARS: Final = 1024
LAST_USER_EXCERPT_MAX: Final = 512
_REDACTION_PATTERNS: Final = (
    re.compile(
        r"(?i)(authorization(?:['\"]?\s*[:=]\s*['\"]?\s*|\s+)"
        r"(?:bearer|basic)\s+)[^\s,;}\"']+"
    ),
    re.compile(
        r"(?i)((?:['\"])?(?:api[_-]?key|token|secret|aws_secret_access_key)"
        r"(?:['\"])?\s*(?::|=|\s)\s*(?:['\"])?)[^\s,;}\"']+"
    ),
    re.compile(r"(?<![A-Za-z0-9])(?:sk-proj-|sk-|ghp_|github_pat_|xox[baprs]-)[A-Za-z0-9_./+=-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    turn: int
    request_id: str
    attempt: int


def context_frames(
    identity: AttemptIdentity, messages: list[dict[str, str]]
) -> tuple[proto.LlmContextChunk, ...]:
    return context_chunks(identity, messages)


def last_user_excerpt(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content", "")[-LAST_USER_EXCERPT_MAX:]
    return ""


def emit_context(
    emit: Callable[[proto.Frame], None],
    identity: AttemptIdentity,
    messages: list[dict[str, str]],
) -> bool:
    try:
        for context_frame in context_frames(identity, messages):
            emit(context_frame)
    except (TransportError, ValueError) as exc:
        try:
            emit(failure_frame(identity, context_logging_error(identity, exc)))
        except (TransportError, ValueError):
            return False
    return True


def reservation_error(identity: AttemptIdentity, message: str) -> llm.LlmCallError:
    return llm.LlmCallError(
        message,
        llm.LlmFailureMetadata(category="reservation_error", attempt=identity.attempt),
    )


def context_logging_error(identity: AttemptIdentity, error: Exception) -> llm.LlmCallError:
    return llm.LlmCallError(
        f"context logging failed: {type(error).__name__}: {error}",
        llm.LlmFailureMetadata(
            category="context_logging_error",
            attempt=identity.attempt,
            error_class=type(error).__name__,
        ),
    )


def safe_error_text(value: str) -> str:
    redacted = value
    for pattern in _REDACTION_PATTERNS:
        replacement = r"\1[REDACTED]" if pattern.groups else "[REDACTED]"
        redacted = pattern.sub(replacement, redacted)
    return redacted[:MAX_ERROR_TEXT_CHARS]


def failure_frame(identity: AttemptIdentity, error: llm.LlmCallError) -> proto.LlmAttemptFailure:
    metadata = error.metadata
    category = metadata.category if metadata is not None else "provider_error"
    return proto.LlmAttemptFailure(
        turn=identity.turn,
        request_id=identity.request_id,
        attempt=metadata.attempt if metadata is not None else identity.attempt,
        category=category,
        finish_reason=metadata.finish_reason if metadata is not None else None,
        error_text=safe_error_text(error.error_text),
        error_class=metadata.error_class if metadata is not None else type(error).__name__,
        status_code=metadata.status_code if metadata is not None else None,
        prompt_tokens=metadata.prompt_tokens if metadata is not None else None,
        completion_tokens=metadata.completion_tokens if metadata is not None else None,
        total_tokens=metadata.total_tokens if metadata is not None else None,
        latency_s=metadata.latency_s if metadata is not None else None,
    )
