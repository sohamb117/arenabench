from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from common.protocol import LlmRequest, LlmReservationDecision, LlmResponse, PidAnnounce
from orchestrator.pricing import PricingProfile
from orchestrator.spend_ledger import (
    AttemptKey,
    ReservationRequest,
    Settlement,
    SpendLedger,
    SpendLimits,
    nano_usd_to_usd,
)


@dataclass(frozen=True, slots=True)
class SlotBudgetPolicy:
    model: str
    fallback_models: tuple[str, ...]
    max_output_tokens: int


class BudgetRuntime:
    """Lifecycle-thread adapter from untrusted wire frames to fixed-point accounting."""

    def __init__(
        self,
        limits: SpendLimits,
        profiles: Mapping[str, PricingProfile],
        slots: tuple[int, ...],
        policies: dict[int, SlotBudgetPolicy],
    ) -> None:
        self._ledger = SpendLedger(limits, profiles, slots)
        self._policies = policies

    @staticmethod
    def accepts_capability(announce: PidAnnounce) -> bool:
        return announce.budget_capability_version == 1

    def reserve(self, slot: int, request: LlmRequest) -> LlmReservationDecision:
        if request.prompt_tokens is None or request.max_output_tokens is None:
            return LlmReservationDecision(
                request_id=request.request_id,
                attempt=request.attempt,
                granted=False,
                reason="invalid_reservation_request",
            )
        policy = self._policies[slot]
        if (
            request.model != policy.model
            or tuple(request.fallback_models or ()) != policy.fallback_models
            or request.max_output_tokens != policy.max_output_tokens
        ):
            return LlmReservationDecision(
                request_id=request.request_id,
                attempt=request.attempt,
                granted=False,
                reason="reservation_policy_mismatch",
            )
        models = (request.model, *(request.fallback_models or ()))
        decision = self._ledger.reserve(
            ReservationRequest(
                key=AttemptKey(slot=slot, request_id=request.request_id, attempt=request.attempt),
                models=models,
                prompt_tokens=request.prompt_tokens,
                max_output_tokens=request.max_output_tokens,
            )
        )
        return LlmReservationDecision(
            request_id=request.request_id,
            attempt=request.attempt,
            granted=decision.granted,
            reason=decision.reason,
            reserved_nano_usd=decision.reserved_nano_usd,
        )

    def settle(self, slot: int, response: LlmResponse) -> str | None:
        consistent = response.total_tokens == response.prompt_tokens + response.completion_tokens
        self._ledger.settle(
            Settlement(
                key=AttemptKey(
                    slot=slot,
                    request_id=response.request_id,
                    attempt=response.attempt,
                ),
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                consistent=consistent,
            )
        )
        return self._ledger.exhaustion_reason(slot)

    def spend_usd(self) -> float:
        return nano_usd_to_usd(self._ledger.estimated_spend_nano_usd)

    def spend_by_slot_usd(self) -> dict[int, float]:
        return {
            slot: nano_usd_to_usd(value)
            for slot, value in self._ledger.estimated_spend_by_slot_nano_usd.items()
        }

    def commit_active_holds(self) -> None:
        self._ledger.commit_active_holds()
