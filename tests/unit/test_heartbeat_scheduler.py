from __future__ import annotations

import math

from common.ids import make_agent_slot
from orchestrator.heartbeat_scheduler import AgentSchedulerState, next_fire_due_in_s, should_fire

NOW = 10.0
INTERVAL = 5.0
OLDER_HEARTBEAT = 2.0
RECENT_HEARTBEAT = 9.0
NEAR_HEARTBEAT = 8.0
NEXT_FIRE_DUE_S = 3.0


def _state(
    *,
    last_heartbeat_ts_monotonic: float,
    llm_call_in_flight: bool,
) -> AgentSchedulerState:
    return AgentSchedulerState(
        slot=make_agent_slot(0),
        last_heartbeat_ts_monotonic=last_heartbeat_ts_monotonic,
        llm_call_in_flight=llm_call_in_flight,
    )


def test_should_fire_true_when_interval_passed_since_last_heartbeat() -> None:
    state = _state(last_heartbeat_ts_monotonic=OLDER_HEARTBEAT, llm_call_in_flight=False)

    assert should_fire(state, NOW, INTERVAL) is True


def test_should_fire_false_when_interval_not_yet_elapsed() -> None:
    state = _state(last_heartbeat_ts_monotonic=RECENT_HEARTBEAT, llm_call_in_flight=False)

    assert should_fire(state, NOW, INTERVAL) is False


def test_should_fire_false_when_llm_call_in_flight_even_if_interval_passed() -> None:
    state = _state(last_heartbeat_ts_monotonic=OLDER_HEARTBEAT, llm_call_in_flight=True)

    assert should_fire(state, NOW, INTERVAL) is False


def test_should_fire_ignores_agent_activity_when_gating_periodic_tick() -> None:
    """Regression: activity used to reset the heartbeat clock, so heartbeats
    never fired in an actively-running match (agents emit frames every ~1-5s,
    interval is 120s). Fixed: heartbeat cadence is purely periodic per plan §2.
    """
    state = _state(last_heartbeat_ts_monotonic=OLDER_HEARTBEAT, llm_call_in_flight=False)

    assert should_fire(state, NOW, INTERVAL) is True


def test_next_fire_due_in_s_returns_positive_float_when_partway_through_interval() -> None:
    state = _state(last_heartbeat_ts_monotonic=NEAR_HEARTBEAT, llm_call_in_flight=False)

    assert next_fire_due_in_s(state, NOW, INTERVAL) == NEXT_FIRE_DUE_S


def test_next_fire_due_in_s_returns_zero_when_already_due() -> None:
    state = _state(last_heartbeat_ts_monotonic=OLDER_HEARTBEAT, llm_call_in_flight=False)

    assert next_fire_due_in_s(state, NOW, INTERVAL) == 0.0


def test_next_fire_due_in_s_returns_inf_when_llm_in_flight() -> None:
    state = _state(last_heartbeat_ts_monotonic=OLDER_HEARTBEAT, llm_call_in_flight=True)

    assert next_fire_due_in_s(state, NOW, INTERVAL) == math.inf
