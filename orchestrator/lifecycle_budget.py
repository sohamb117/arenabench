from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from common.protocol import (
    BudgetCapability,
    Envelope,
    Frame,
    LlmRequest,
    LlmResponse,
    PidAnnounce,
    Shutdown,
)
from orchestrator._lifecycle_state import MatchContext, MatchSpendSummary


class EnvelopeFactory(Protocol):
    def __call__(self, kind: str, data: Frame, dst: str = "guest_probe") -> Envelope: ...


@dataclass(frozen=True, slots=True)
class BudgetExhausted:
    reason: str


def negotiate_capability(
    ctx: MatchContext,
    slot: int,
    port: int,
    announce: PidAnnounce,
    mk_env: EnvelopeFactory,
) -> bool:
    capable = announce.budget_capability_version == 1
    if ctx.budget_runtime is not None and not capable:
        return False
    if capable:
        capability = BudgetCapability(version=1, enabled=ctx.budget_runtime is not None)
        ctx.vsock_server.send_frame(
            port,
            mk_env("budget_capability", capability, dst=f"agent{slot}"),
        )
    return True


def process_budget_frame(
    ctx: MatchContext,
    slot: int,
    port: int,
    env: Envelope,
    mk_env: EnvelopeFactory,
) -> BudgetExhausted | None:
    runtime = ctx.budget_runtime
    if runtime is None:
        return None
    if isinstance(env.data, LlmRequest):
        decision = runtime.reserve(slot, env.data)
        decision_env = mk_env("llm_reservation_decision", decision, dst=f"agent{slot}")
        ctx.vsock_server.send_frame(
            port,
            decision_env,
        )
        ctx.logger.write_envelope(decision_env)
        if not decision.granted and decision.reason.endswith("budget_exhausted"):
            return BudgetExhausted(reason=decision.reason)
    elif isinstance(env.data, LlmResponse):
        reason = runtime.settle(slot, env.data)
        if reason is not None:
            return BudgetExhausted(reason=reason)
    return None


def broadcast_budget_shutdown(ctx: MatchContext, mk_env: EnvelopeFactory, reason: str) -> None:
    for slot, port in ctx.agent_ports.items():
        ctx.vsock_server.send_frame(
            port,
            mk_env("shutdown", Shutdown(reason=reason), dst=f"agent{slot}"),
        )


def spend_summary(ctx: MatchContext) -> MatchSpendSummary:
    runtime = ctx.budget_runtime
    if runtime is not None:
        runtime.commit_active_holds()
    by_slot = (
        {str(slot): value for slot, value in runtime.spend_by_slot_usd().items()}
        if runtime is not None
        else {str(slot): 0.0 for slot in range(ctx.match_config.n_agents)}
    )
    return MatchSpendSummary(
        estimated_spend_usd=runtime.spend_usd() if runtime is not None else 0.0,
        estimated_spend_by_agent_usd=by_slot,
        budget_usd=ctx.match_config.budget_usd,
        per_agent_budget_usd=ctx.match_config.per_agent_budget_usd,
    )
