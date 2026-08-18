from __future__ import annotations

from orchestrator.pricing import PricingProfile
from orchestrator.spend_ledger import (
    AttemptKey,
    ReservationRequest,
    Settlement,
    SpendLedger,
    SpendLimits,
    estimate_prompt_tokens,
)

PROMPT_ESTIMATE = 11
RESERVATION_NANO_USD = 16
RETRY_EXPOSURE_NANO_USD = 20


def _profile(rate: float = 0.000_000_001) -> PricingProfile:
    return PricingProfile(
        model="provider/model",
        input_usd_per_token=rate,
        output_usd_per_token=rate,
    )


def _request(
    slot: int, attempt: int, prompt_tokens: int, max_output_tokens: int
) -> ReservationRequest:
    return ReservationRequest(
        key=AttemptKey(slot=slot, request_id="request", attempt=attempt),
        models=("provider/model",),
        prompt_tokens=prompt_tokens,
        max_output_tokens=max_output_tokens,
    )


def test_prompt_estimate_applies_safety_factor_and_never_shrinks_report() -> None:
    assert estimate_prompt_tokens(10) == PROMPT_ESTIMATE
    assert estimate_prompt_tokens(0) == 0


def test_reservation_allows_exact_global_cap_boundary() -> None:
    ledger = SpendLedger(
        SpendLimits(budget_usd=0.000_000_016_9, per_agent_budget_usd=None),
        {"provider/model": _profile()},
        slots=(0,),
    )

    decision = ledger.reserve(_request(0, 0, prompt_tokens=10, max_output_tokens=5))

    assert decision.granted is True
    assert decision.reserved_nano_usd == RESERVATION_NANO_USD


def test_cost_rounds_up_after_cap_rounds_down() -> None:
    ledger = SpendLedger(
        SpendLimits(budget_usd=0.000_000_016_9, per_agent_budget_usd=None),
        {"provider/model": _profile(rate=0.000_000_001_01)},
        slots=(0,),
    )

    decision = ledger.reserve(_request(0, 0, prompt_tokens=10, max_output_tokens=5))

    assert decision.granted is False
    assert decision.reason == "global_budget_exhausted"


def test_global_and_per_slot_caps_are_enforced_independently() -> None:
    ledger = SpendLedger(
        SpendLimits(budget_usd=0.000_000_040, per_agent_budget_usd=0.000_000_020),
        {"provider/model": _profile()},
        slots=(0, 1),
    )

    first = ledger.reserve(_request(0, 0, prompt_tokens=9, max_output_tokens=5))
    same_slot = ledger.reserve(
        ReservationRequest(
            key=AttemptKey(slot=0, request_id="other", attempt=0),
            models=("provider/model",),
            prompt_tokens=9,
            max_output_tokens=5,
        )
    )
    other_slot = ledger.reserve(_request(1, 0, prompt_tokens=9, max_output_tokens=5))

    assert first.granted is True
    assert same_slot.reason == "agent_budget_exhausted"
    assert other_slot.granted is True


def test_global_cap_counts_active_holds_from_other_slots() -> None:
    ledger = SpendLedger(
        SpendLimits(budget_usd=0.000_000_020, per_agent_budget_usd=None),
        {"provider/model": _profile()},
        slots=(0, 1),
    )

    first = ledger.reserve(_request(0, 0, prompt_tokens=4, max_output_tokens=5))
    second = ledger.reserve(_request(1, 0, prompt_tokens=4, max_output_tokens=5))
    third = ledger.reserve(
        ReservationRequest(
            key=AttemptKey(slot=1, request_id="simultaneous", attempt=0),
            models=("provider/model",),
            prompt_tokens=0,
            max_output_tokens=1,
        )
    )

    assert first.granted is True
    assert second.granted is True
    assert third.reason == "global_budget_exhausted"


def test_retry_commits_prior_hold_before_next_reservation() -> None:
    ledger = SpendLedger(
        SpendLimits(budget_usd=0.000_000_020, per_agent_budget_usd=None),
        {"provider/model": _profile()},
        slots=(0,),
    )
    request = _request(0, 0, prompt_tokens=4, max_output_tokens=5)

    first = ledger.reserve(request)
    second = ledger.reserve(_request(0, 1, prompt_tokens=4, max_output_tokens=5))
    third = ledger.reserve(_request(0, 2, prompt_tokens=4, max_output_tokens=5))

    assert first.granted is True
    assert second.granted is True
    assert third.reason == "global_budget_exhausted"
    assert ledger.estimated_spend_nano_usd == RETRY_EXPOSURE_NANO_USD


def test_duplicate_and_stale_attempts_never_replay_grants() -> None:
    ledger = SpendLedger(
        SpendLimits(budget_usd=1.0, per_agent_budget_usd=None),
        {"provider/model": _profile()},
        slots=(0,),
    )
    first = ledger.reserve(_request(0, 0, prompt_tokens=1, max_output_tokens=1))

    duplicate = ledger.reserve(_request(0, 0, prompt_tokens=1, max_output_tokens=1))
    newer = ledger.reserve(_request(0, 1, prompt_tokens=1, max_output_tokens=1))
    stale = ledger.reserve(_request(0, 0, prompt_tokens=1, max_output_tokens=1))

    assert first.granted is True
    assert duplicate.reason == "duplicate_attempt"
    assert newer.granted is True
    assert stale.reason == "duplicate_attempt"


def test_settlement_never_commits_less_than_reserved_hold() -> None:
    ledger = SpendLedger(
        SpendLimits(budget_usd=1.0, per_agent_budget_usd=None),
        {"provider/model": _profile()},
        slots=(0, 1),
    )
    granted = ledger.reserve(_request(0, 0, prompt_tokens=10, max_output_tokens=5))

    ledger.settle(
        Settlement(
            key=AttemptKey(slot=0, request_id="request", attempt=0),
            prompt_tokens=0,
            completion_tokens=0,
            consistent=False,
        )
    )

    assert ledger.estimated_spend_nano_usd == granted.reserved_nano_usd
    assert ledger.estimated_spend_by_slot_nano_usd == {0: 16, 1: 0}


def test_unresolved_hold_is_committed_when_match_finishes() -> None:
    ledger = SpendLedger(
        SpendLimits(budget_usd=1.0, per_agent_budget_usd=None),
        {"provider/model": _profile()},
        slots=(0,),
    )
    granted = ledger.reserve(_request(0, 0, prompt_tokens=10, max_output_tokens=5))

    ledger.commit_active_holds()

    assert ledger.estimated_spend_nano_usd == granted.reserved_nano_usd
    assert ledger.active_hold_nano_usd == 0
