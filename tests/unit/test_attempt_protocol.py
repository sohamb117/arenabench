from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.protocol import (
    MAX_FRAME_BYTES,
    Envelope,
    LlmAttemptFailure,
    LlmContextSnapshot,
    LlmMessage,
    serialize_envelope,
)


def test_context_snapshot_serializes_exact_unicode_messages() -> None:
    messages = [
        LlmMessage(role="user", content="こんにちは 🔐\nline two"),
        LlmMessage(role="assistant", content="café — 그대로"),
    ]
    frame = LlmContextSnapshot(turn=3, request_id="req-ü", attempt=1, messages=messages)
    envelope = Envelope(
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        seq=4,
        src="agent0",
        dst="orchestrator",
        kind=frame.kind,
        data=frame,
    )

    serialized = serialize_envelope(envelope)

    assert "こんにちは 🔐" in serialized
    assert envelope.data == frame
    assert frame.messages == messages


def test_attempt_frames_reject_provider_specific_or_credential_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LlmAttemptFailure.model_validate(
            {
                "turn": 0,
                "request_id": "req-1",
                "attempt": 0,
                "category": "provider_error",
                "error_text": "failure",
                "api_key": "secret",
                "authorization": "Bearer secret",
                "provider_headers": {"x-request-id": "provider-id"},
            }
        )


def test_context_snapshot_over_frame_cap_is_rejected() -> None:
    frame = LlmContextSnapshot(
        turn=0,
        request_id="req-1",
        attempt=0,
        messages=[LlmMessage(role="user", content="界" * MAX_FRAME_BYTES)],
    )
    envelope = Envelope(
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        seq=0,
        src="agent0",
        dst="orchestrator",
        kind=frame.kind,
        data=frame,
    )

    with pytest.raises(ValueError, match="serialized frame exceeds"):
        serialize_envelope(envelope)
