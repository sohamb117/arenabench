from common.ids import make_agent_slot
from orchestrator.winner import resolve

GRACE_PERIOD_S = 5.0
MAX_DURATION_S = 60.0


def test_resolve_mutual_destruction() -> None:
    outcome = resolve(
        alive=set(),
        elapsed_s=10.0,
        grace_started_s=None,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=MAX_DURATION_S,
        last_alive_count=2,
    )
    assert outcome.result == "draw"
    assert outcome.cause == "mutual_destruction"
    assert outcome.winner is None
    assert outcome.alive_at_decision == ()


def test_resolve_survivor_died_in_grace() -> None:
    outcome = resolve(
        alive=set(),
        elapsed_s=12.0,
        grace_started_s=10.0,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=MAX_DURATION_S,
        last_alive_count=1,
    )
    assert outcome.result == "draw"
    assert outcome.cause == "survivor_died_in_grace"
    assert outcome.winner is None


def test_resolve_grace_started() -> None:
    slot = make_agent_slot(0)
    outcome = resolve(
        alive={slot},
        elapsed_s=10.0,
        grace_started_s=None,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=MAX_DURATION_S,
        last_alive_count=2,
    )
    assert outcome.result == "in_progress"
    assert outcome.cause == "grace_started"
    assert outcome.winner is None
    assert outcome.alive_at_decision == (slot,)


def test_resolve_grace_in_progress() -> None:
    slot = make_agent_slot(0)
    outcome = resolve(
        alive={slot},
        elapsed_s=12.0,
        grace_started_s=10.0,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=MAX_DURATION_S,
        last_alive_count=1,
    )
    assert outcome.result == "in_progress"
    assert outcome.cause == "grace_in_progress"
    assert outcome.winner is None


def test_resolve_victory_opponent_crashed() -> None:
    slot = make_agent_slot(1)
    outcome = resolve(
        alive={slot},
        elapsed_s=16.0,
        grace_started_s=10.0,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=MAX_DURATION_S,
        last_alive_count=2,
    )
    assert outcome.result == "victory"
    assert outcome.cause == "opponent_crashed"
    assert outcome.winner == slot


def test_resolve_victory_solo_survivor() -> None:
    slot = make_agent_slot(1)
    outcome = resolve(
        alive={slot},
        elapsed_s=16.0,
        grace_started_s=10.0,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=MAX_DURATION_S,
        last_alive_count=1,
    )
    assert outcome.result == "victory"
    assert outcome.cause == "solo_survivor"
    assert outcome.winner == slot


def test_resolve_timeout() -> None:
    slot1 = make_agent_slot(0)
    slot2 = make_agent_slot(1)
    outcome = resolve(
        alive={slot1, slot2},
        elapsed_s=61.0,
        grace_started_s=None,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=MAX_DURATION_S,
        last_alive_count=2,
    )
    assert outcome.result == "timeout"
    assert outcome.cause == "max_duration_exceeded"
    assert outcome.winner is None
    assert outcome.alive_at_decision == (slot1, slot2)


def test_resolve_match_continues() -> None:
    slot1 = make_agent_slot(0)
    slot2 = make_agent_slot(1)
    outcome = resolve(
        alive={slot1, slot2},
        elapsed_s=30.0,
        grace_started_s=None,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=MAX_DURATION_S,
        last_alive_count=2,
    )
    assert outcome.result == "in_progress"
    assert outcome.cause == "match_continues"
    assert outcome.winner is None


def test_resolve_inconsistent_state() -> None:
    slot1 = make_agent_slot(0)
    slot2 = make_agent_slot(1)
    outcome = resolve(
        alive={slot1, slot2},
        elapsed_s=30.0,
        grace_started_s=10.0,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=MAX_DURATION_S,
        last_alive_count=2,
    )
    assert outcome.result == "error"
    assert outcome.cause == "inconsistent_state_grace_with_multi_alive"
    assert outcome.winner is None
