from __future__ import annotations

import math

from common.ids import make_agent_slot
from orchestrator.heartbeat_scheduler import AgentSchedulerState, next_fire_due_in_s, should_fire

NOW = 10.0
INTERVAL = 5.0
NEXT_FIRE_DUE_S = 3.0
RECENT_ACTIVITY = 10.0
OLDER_ACTIVITY = 2.0
RECENT_HEARTBEAT = 9.0
OLDER_HEARTBEAT = 0.0


def _state(
    *,
    last_activity_ts_monotonic: float,
    last_heartbeat_ts_monotonic: float,
    llm_call_in_flight: bool,
) -> AgentSchedulerState:
    return AgentSchedulerState(
        slot=make_agent_slot(0),
        last_activity_ts_monotonic=last_activity_ts_monotonic,
        last_heartbeat_ts_monotonic=last_heartbeat_ts_monotonic,
        llm_call_in_flight=llm_call_in_flight,
    )


def test_should_fire_false_when_last_activity_is_now() -> None:
    state = _state(
        last_activity_ts_monotonic=RECENT_ACTIVITY,
        last_heartbeat_ts_monotonic=OLDER_HEARTBEAT,
        llm_call_in_flight=False,
    )

    assert should_fire(state, NOW, INTERVAL) is False


def test_should_fire_true_when_interval_passed_and_llm_not_in_flight() -> None:
    state = _state(
        last_activity_ts_monotonic=OLDER_ACTIVITY,
        last_heartbeat_ts_monotonic=OLDER_HEARTBEAT,
        llm_call_in_flight=False,
    )

    assert should_fire(state, NOW, INTERVAL) is True


def test_should_fire_false_when_interval_passed_but_llm_in_flight() -> None:
    state = _state(
        last_activity_ts_monotonic=OLDER_ACTIVITY,
        last_heartbeat_ts_monotonic=OLDER_HEARTBEAT,
        llm_call_in_flight=True,
    )

    assert should_fire(state, NOW, INTERVAL) is False


def test_should_fire_false_after_recent_heartbeat() -> None:
    state = _state(
        last_activity_ts_monotonic=OLDER_ACTIVITY,
        last_heartbeat_ts_monotonic=RECENT_HEARTBEAT,
        llm_call_in_flight=False,
    )

    assert should_fire(state, NOW, INTERVAL) is False


def test_should_fire_uses_most_recent_timestamp_between_activity_and_heartbeat() -> None:
    state = _state(
        last_activity_ts_monotonic=OLDER_ACTIVITY,
        last_heartbeat_ts_monotonic=RECENT_HEARTBEAT,
        llm_call_in_flight=False,
    )

    assert should_fire(state, NOW, INTERVAL) is False


def test_should_fire_true_when_interval_passed_since_recent_activity_despite_old_hb() -> None:
    state = _state(
        last_activity_ts_monotonic=OLDER_ACTIVITY,
        last_heartbeat_ts_monotonic=1.0,
        llm_call_in_flight=False,
    )

    assert should_fire(state, NOW, INTERVAL) is True


def test_next_fire_due_in_s_returns_positive_float_when_ready() -> None:
    state = _state(
        last_activity_ts_monotonic=8.0,
        last_heartbeat_ts_monotonic=OLDER_HEARTBEAT,
        llm_call_in_flight=False,
    )

    assert next_fire_due_in_s(state, NOW, INTERVAL) == NEXT_FIRE_DUE_S


def test_next_fire_due_in_s_returns_inf_when_llm_in_flight() -> None:
    state = _state(
        last_activity_ts_monotonic=OLDER_ACTIVITY,
        last_heartbeat_ts_monotonic=OLDER_HEARTBEAT,
        llm_call_in_flight=True,
    )

    assert next_fire_due_in_s(state, NOW, INTERVAL) == math.inf
