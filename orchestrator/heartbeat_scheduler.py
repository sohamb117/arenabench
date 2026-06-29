from __future__ import annotations

from dataclasses import dataclass

from common.ids import AgentSlot


@dataclass(frozen=True, slots=True)
class AgentSchedulerState:
    slot: AgentSlot
    last_activity_ts_monotonic: float
    last_heartbeat_ts_monotonic: float
    llm_call_in_flight: bool


def should_fire(
    state: AgentSchedulerState,
    now_monotonic: float,
    heartbeat_interval_s: float,
) -> bool:
    if state.llm_call_in_flight:
        return False
    most_recent_ts = max(
        state.last_activity_ts_monotonic,
        state.last_heartbeat_ts_monotonic,
    )
    return now_monotonic - most_recent_ts >= heartbeat_interval_s


def next_fire_due_in_s(
    state: AgentSchedulerState,
    now_monotonic: float,
    heartbeat_interval_s: float,
) -> float:
    if state.llm_call_in_flight:
        return float("inf")
    most_recent_ts = max(
        state.last_activity_ts_monotonic,
        state.last_heartbeat_ts_monotonic,
    )
    return max(0.0, heartbeat_interval_s - (now_monotonic - most_recent_ts))
