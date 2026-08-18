from __future__ import annotations

from common.protocol import LlmRequest, LlmResponse, PidAnnounce
from orchestrator.budget_runtime import BudgetRuntime, SlotBudgetPolicy
from orchestrator.pricing import PricingProfile
from orchestrator.spend_ledger import SpendLimits


def _runtime() -> BudgetRuntime:
    return BudgetRuntime(
        limits=SpendLimits(budget_usd=1.0, per_agent_budget_usd=0.75),
        profiles={
            "provider/primary": PricingProfile(
                model="provider/primary",
                input_usd_per_token=0.001,
                output_usd_per_token=0.002,
            )
        },
        slots=(0, 1),
        policies={
            0: SlotBudgetPolicy(model="provider/primary", fallback_models=(), max_output_tokens=10),
            1: SlotBudgetPolicy(model="provider/primary", fallback_models=(), max_output_tokens=10),
        },
    )


def _request() -> LlmRequest:
    return LlmRequest(
        turn=0,
        request_id="request",
        model="provider/primary",
        messages_count=1,
        prompt_chars=10,
        prompt_tokens=10,
        max_output_tokens=10,
        fallback_models=None,
        temperature=0.0,
        attempt=0,
    )


def test_capability_requires_current_guest_version() -> None:
    runtime = _runtime()
    stale = PidAnnounce(
        pid=1,
        user="agent0",
        uid=1,
        hostname="guest",
        parser="json",
        model="provider/primary",
    )

    assert runtime.accepts_capability(stale) is False
    assert runtime.accepts_capability(stale.model_copy(update={"budget_capability_version": 1}))


def test_request_uses_receiving_slot_for_reservation() -> None:
    runtime = _runtime()

    decision = runtime.reserve(slot=1, request=_request())

    assert decision.granted is True
    assert runtime.spend_by_slot_usd()[0] == 0.0
    assert runtime.spend_by_slot_usd()[1] > 0.0


def test_request_without_bounded_quote_fields_is_denied() -> None:
    runtime = _runtime()
    request = _request().model_copy(update={"prompt_tokens": None})

    decision = runtime.reserve(slot=0, request=request)

    assert decision.granted is False
    assert decision.reason == "invalid_reservation_request"


def test_request_cannot_override_trusted_slot_policy() -> None:
    runtime = _runtime()
    request = _request().model_copy(update={"max_output_tokens": 1})

    decision = runtime.reserve(slot=0, request=request)

    assert decision.granted is False
    assert decision.reason == "reservation_policy_mismatch"


def test_request_cannot_override_trusted_model_policy() -> None:
    runtime = _runtime()
    request = _request().model_copy(update={"model": "provider/different"})

    decision = runtime.reserve(slot=0, request=request)

    assert decision.granted is False
    assert decision.reason == "reservation_policy_mismatch"


def test_request_with_fallback_is_denied_for_capped_match() -> None:
    runtime = _runtime()
    request = _request().model_copy(update={"fallback_models": ["provider/fallback"]})

    decision = runtime.reserve(slot=0, request=request)

    assert decision.granted is False
    assert decision.reason == "reservation_policy_mismatch"


def test_response_settles_correlated_attempt_without_refund() -> None:
    runtime = _runtime()
    decision = runtime.reserve(slot=0, request=_request())
    response = LlmResponse(
        turn=0,
        request_id="request",
        attempt=0,
        content="ok",
        parser="json",
        parse_ok=True,
        prompt_tokens=1,
        completion_tokens=1,
        total_tokens=2,
        latency_s=0.1,
    )

    runtime.settle(slot=0, response=response)

    assert runtime.spend_usd() == decision.reserved_nano_usd / 1_000_000_000
