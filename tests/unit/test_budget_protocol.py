from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from common.protocol import (
    BudgetCapability,
    Envelope,
    LlmRequest,
    LlmReservationDecision,
    parse_envelope,
    serialize_envelope,
)

PROMPT_TOKENS = 40
MAX_OUTPUT_TOKENS = 200


def _envelope(data: BudgetCapability | LlmReservationDecision) -> Envelope:
    return Envelope(
        ts=datetime.now(UTC),
        seq=1,
        src="orchestrator",
        dst="agent0",
        kind=data.kind,
        data=data,
    )


def test_budget_capability_round_trips() -> None:
    frame = BudgetCapability(version=1, enabled=True)

    parsed = parse_envelope(serialize_envelope(_envelope(frame)))

    assert parsed.data == frame


def test_reservation_decision_round_trips_with_attempt_correlation() -> None:
    frame = LlmReservationDecision(
        request_id="request",
        attempt=2,
        granted=True,
        reason="granted",
        reserved_nano_usd=42,
    )

    parsed = parse_envelope(serialize_envelope(_envelope(frame)))

    assert parsed.data == frame


def test_llm_request_accepts_bounded_budget_fields() -> None:
    request = LlmRequest(
        turn=1,
        request_id="request",
        model="provider/primary",
        messages_count=2,
        prompt_chars=100,
        prompt_tokens=PROMPT_TOKENS,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        fallback_models=["provider/fallback"],
        temperature=0.0,
        attempt=1,
    )

    assert request.prompt_tokens == PROMPT_TOKENS
    assert request.max_output_tokens == MAX_OUTPUT_TOKENS
    assert request.fallback_models == ["provider/fallback"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prompt_tokens", -1),
        ("max_output_tokens", 0),
        ("fallback_models", ["model"] * 17),
        ("attempt", -1),
    ],
)
def test_llm_request_rejects_unbounded_budget_fields(field: str, value: object) -> None:
    payload: dict[str, object] = {
        "turn": 1,
        "request_id": "request",
        "model": "provider/primary",
        "messages_count": 2,
        "prompt_chars": 100,
        "prompt_tokens": 40,
        "max_output_tokens": 200,
        "fallback_models": [],
        "temperature": 0.0,
        "attempt": 1,
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        LlmRequest.model_validate(payload)


def test_old_llm_request_remains_valid_without_budget_fields() -> None:
    request = LlmRequest(
        turn=1,
        request_id="request",
        model="provider/primary",
        messages_count=2,
        prompt_chars=100,
        temperature=0.0,
    )

    assert request.prompt_tokens is None
    assert request.max_output_tokens is None
    assert request.fallback_models is None
