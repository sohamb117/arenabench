from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Final

from orchestrator.pricing import PricingProfile

NanoUsd = int
_NANO_PER_USD: Final = Decimal(1_000_000_000)
_PROMPT_SAFETY_FACTOR: Final = Decimal("1.1")


@dataclass(frozen=True, slots=True)
class SpendLimits:
    budget_usd: float | None
    per_agent_budget_usd: float | None


@dataclass(frozen=True, slots=True)
class AttemptKey:
    slot: int
    request_id: str
    attempt: int


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    key: AttemptKey
    models: tuple[str, ...]
    prompt_tokens: int
    max_output_tokens: int


@dataclass(frozen=True, slots=True)
class ReservationDecision:
    granted: bool
    reason: str
    reserved_nano_usd: NanoUsd = 0


@dataclass(frozen=True, slots=True)
class Settlement:
    key: AttemptKey
    prompt_tokens: int
    completion_tokens: int
    consistent: bool


class SpendLedger:
    """Mutable reservation state owned exclusively by the match lifecycle thread."""

    def __init__(
        self,
        limits: SpendLimits,
        profiles: Mapping[str, PricingProfile],
        slots: tuple[int, ...],
    ) -> None:
        self._global_cap = _cap_nano(limits.budget_usd)
        self._slot_cap = _cap_nano(limits.per_agent_budget_usd)
        self._profiles = profiles
        self._committed = 0
        self._committed_by_slot = {slot: 0 for slot in slots}
        self._holds: dict[AttemptKey, NanoUsd] = {}
        self._latest_attempt: dict[tuple[int, str], int] = {}
        self._seen: set[AttemptKey] = set()

    def reserve(self, request: ReservationRequest) -> ReservationDecision:
        key = request.key
        if key in self._seen:
            return ReservationDecision(granted=False, reason="duplicate_attempt")
        stream = (key.slot, key.request_id)
        latest = self._latest_attempt.get(stream)
        if latest is not None and key.attempt <= latest:
            return ReservationDecision(granted=False, reason="stale_attempt")
        if latest is not None:
            prior = AttemptKey(slot=key.slot, request_id=key.request_id, attempt=latest)
            self._commit_hold(prior)
        self._seen.add(key)
        self._latest_attempt[stream] = key.attempt

        quote = _quote_nano(request, self._profiles)
        global_exposure = self._committed + sum(self._holds.values())
        if self._global_cap is not None and global_exposure + quote > self._global_cap:
            return ReservationDecision(granted=False, reason="global_budget_exhausted")
        slot_exposure = self._committed_by_slot[key.slot] + sum(
            hold for hold_key, hold in self._holds.items() if hold_key.slot == key.slot
        )
        if self._slot_cap is not None and slot_exposure + quote > self._slot_cap:
            return ReservationDecision(granted=False, reason="agent_budget_exhausted")
        self._holds[key] = quote
        return ReservationDecision(granted=True, reason="granted", reserved_nano_usd=quote)

    def settle(self, settlement: Settlement) -> None:
        self._commit_hold(settlement.key)

    def commit_active_holds(self) -> None:
        for key in tuple(self._holds):
            self._commit_hold(key)

    @property
    def active_hold_nano_usd(self) -> NanoUsd:
        return sum(self._holds.values())

    def exhaustion_reason(self, slot: int) -> str | None:
        if self._global_cap is not None and self.estimated_spend_nano_usd >= self._global_cap:
            return "global_budget_exhausted"
        slot_total = self.estimated_spend_by_slot_nano_usd[slot]
        if self._slot_cap is not None and slot_total >= self._slot_cap:
            return "agent_budget_exhausted"
        return None

    @property
    def estimated_spend_nano_usd(self) -> NanoUsd:
        return self._committed + sum(self._holds.values())

    @property
    def estimated_spend_by_slot_nano_usd(self) -> dict[int, NanoUsd]:
        totals = dict(self._committed_by_slot)
        for key, hold in self._holds.items():
            totals[key.slot] += hold
        return totals

    def _commit_hold(self, key: AttemptKey) -> None:
        hold = self._holds.pop(key, None)
        if hold is None:
            return
        self._committed += hold
        self._committed_by_slot[key.slot] += hold


def estimate_prompt_tokens(reported_tokens: int) -> int:
    if reported_tokens < 0:
        msg = "reported prompt tokens must be non-negative"
        raise ValueError(msg)
    return int(
        (Decimal(reported_tokens) * _PROMPT_SAFETY_FACTOR).to_integral_value(rounding=ROUND_CEILING)
    )


def nano_usd_to_usd(value: NanoUsd) -> float:
    return float(Decimal(value) / _NANO_PER_USD)


def _cap_nano(value: float | None) -> NanoUsd | None:
    if value is None:
        return None
    return int((Decimal(str(value)) * _NANO_PER_USD).to_integral_value(rounding=ROUND_FLOOR))


def _quote_nano(request: ReservationRequest, profiles: Mapping[str, PricingProfile]) -> NanoUsd:
    if request.prompt_tokens < 0 or request.max_output_tokens < 1 or not request.models:
        msg = "invalid reservation request"
        raise ValueError(msg)
    selected = [profiles[model] for model in request.models]
    input_rate = max(Decimal(str(profile.input_usd_per_token)) for profile in selected)
    output_rate = max(Decimal(str(profile.output_usd_per_token)) for profile in selected)
    prompt = Decimal(estimate_prompt_tokens(request.prompt_tokens))
    output = Decimal(request.max_output_tokens)
    cost = ((prompt * input_rate) + (output * output_rate)) * _NANO_PER_USD
    if not math.isfinite(float(cost)):
        msg = "non-finite reservation quote"
        raise ValueError(msg)
    return int(cost.to_integral_value(rounding=ROUND_CEILING))
