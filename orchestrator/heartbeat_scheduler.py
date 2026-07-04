from __future__ import annotations

from dataclasses import dataclass

from common.ids import AgentSlot


@dataclass(frozen=True, slots=True)
class AgentSchedulerState:
    slot: AgentSlot
    last_heartbeat_ts_monotonic: float
    llm_call_in_flight: bool


def should_fire(
    state: AgentSchedulerState,
    now_monotonic: float,
    heartbeat_interval_s: float,
) -> bool:
    # Purely periodic per plan §2 "fixed interval". Activity does NOT reset
    # the heartbeat clock — silence-based liveness (§R11, 5s default) already
    # handles the "agent got stuck" case with a much tighter window.
    if state.llm_call_in_flight:
        return False
    return now_monotonic - state.last_heartbeat_ts_monotonic >= heartbeat_interval_s


def next_fire_due_in_s(
    state: AgentSchedulerState,
    now_monotonic: float,
    heartbeat_interval_s: float,
) -> float:
    if state.llm_call_in_flight:
        return float("inf")
    return max(0.0, heartbeat_interval_s - (now_monotonic - state.last_heartbeat_ts_monotonic))
