from __future__ import annotations

from dataclasses import dataclass

from common import protocol as proto
from harness import llm

_MAX_ERROR_TEXT_CHARS = 1024


@dataclass(frozen=True, slots=True)
class AttemptIdentity:
    turn: int
    request_id: str
    attempt: int


def context_snapshot(
    identity: AttemptIdentity, messages: list[dict[str, str]]
) -> proto.LlmContextSnapshot:
    return proto.LlmContextSnapshot(
        turn=identity.turn,
        request_id=identity.request_id,
        attempt=identity.attempt,
        messages=[proto.LlmMessage.model_validate(message) for message in messages],
    )


def context_too_large(identity: AttemptIdentity) -> llm.LlmCallError:
    return llm.LlmCallError(
        "exact LLM context exceeds protocol frame limit",
        llm.LlmFailureMetadata(category="context_too_large", attempt=identity.attempt),
    )


def reservation_error(identity: AttemptIdentity, message: str) -> llm.LlmCallError:
    return llm.LlmCallError(
        message,
        llm.LlmFailureMetadata(category="reservation_error", attempt=identity.attempt),
    )


def failure_frame(identity: AttemptIdentity, error: llm.LlmCallError) -> proto.LlmAttemptFailure:
    metadata = error.metadata
    category = metadata.category if metadata is not None else "provider_error"
    return proto.LlmAttemptFailure(
        turn=identity.turn,
        request_id=identity.request_id,
        attempt=metadata.attempt if metadata is not None else identity.attempt,
        category=category,
        finish_reason=metadata.finish_reason if metadata is not None else None,
        error_text=error.error_text[:_MAX_ERROR_TEXT_CHARS],
        error_class=metadata.error_class if metadata is not None else type(error).__name__,
        status_code=metadata.status_code if metadata is not None else None,
        prompt_tokens=metadata.prompt_tokens if metadata is not None else None,
        completion_tokens=metadata.completion_tokens if metadata is not None else None,
        total_tokens=metadata.total_tokens if metadata is not None else None,
        latency_s=metadata.latency_s if metadata is not None else None,
    )
