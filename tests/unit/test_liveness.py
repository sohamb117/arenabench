from common.ids import AgentSlot
from orchestrator.liveness import (
    AgentLivenessState,
    LivenessThresholds,
    cause_of_death,
    is_alive,
)

NOW = 1000.0
TH = LivenessThresholds()
SLOT = AgentSlot(0)

# Time constants to avoid PLR2004 magic values
RECENT_OFFSET = 1.0
STALE_OFFSET_SILENCE = TH.silence_threshold_s + 1.0
STALE_OFFSET_LLM_MAX = TH.llm_max_s + 1.0
STALE_OFFSET_KILL0 = TH.kill0_max_age_s + 1.0
LLM_IN_FLIGHT_OFFSET = 10.0


def make_state(
    vsock_connected: bool = True,
    last_frame_ts_monotonic: float = NOW - RECENT_OFFSET,
    llm_call_start_ts_monotonic: float | None = None,
    kill0_alive: bool = True,
    kill0_ts_monotonic: float = NOW - RECENT_OFFSET,
) -> AgentLivenessState:
    return AgentLivenessState(
        slot=SLOT,
        vsock_connected=vsock_connected,
        last_frame_ts_monotonic=last_frame_ts_monotonic,
        llm_call_start_ts_monotonic=llm_call_start_ts_monotonic,
        kill0_alive=kill0_alive,
        kill0_ts_monotonic=kill0_ts_monotonic,
    )


def test_alive_all_signals_positive() -> None:
    state = make_state()
    assert is_alive(state, NOW, TH) is True
    assert cause_of_death(state, NOW, TH) == "alive"


def test_dead_vsock_disconnect() -> None:
    state = make_state(vsock_connected=False)
    assert is_alive(state, NOW, TH) is False
    assert cause_of_death(state, NOW, TH) == "vsock_disconnect"


def test_dead_kill0_dead() -> None:
    state = make_state(kill0_alive=False)
    assert is_alive(state, NOW, TH) is False
    assert cause_of_death(state, NOW, TH) == "kill0_dead"


def test_dead_silence_timeout() -> None:
    state = make_state(last_frame_ts_monotonic=NOW - STALE_OFFSET_SILENCE)
    assert is_alive(state, NOW, TH) is False
    assert cause_of_death(state, NOW, TH) == "silence_timeout"


def test_alive_llm_in_flight() -> None:
    state = make_state(
        last_frame_ts_monotonic=NOW - STALE_OFFSET_SILENCE - LLM_IN_FLIGHT_OFFSET,
        llm_call_start_ts_monotonic=NOW - LLM_IN_FLIGHT_OFFSET,
    )
    assert is_alive(state, NOW, TH) is True
    assert cause_of_death(state, NOW, TH) == "alive"


def test_dead_llm_max_exceeded() -> None:
    state = make_state(
        last_frame_ts_monotonic=NOW - STALE_OFFSET_SILENCE - LLM_IN_FLIGHT_OFFSET,
        llm_call_start_ts_monotonic=NOW - STALE_OFFSET_LLM_MAX,
    )
    assert is_alive(state, NOW, TH) is False
    assert cause_of_death(state, NOW, TH) == "silence_timeout"


def test_kill0_stale_alone_is_alive_no_silence() -> None:
    state = make_state(kill0_ts_monotonic=NOW - STALE_OFFSET_KILL0)
    assert is_alive(state, NOW, TH) is True
    assert cause_of_death(state, NOW, TH) == "alive"


def test_dead_kill0_stale_and_silence() -> None:
    state = make_state(
        kill0_ts_monotonic=NOW - STALE_OFFSET_KILL0,
        last_frame_ts_monotonic=NOW - STALE_OFFSET_SILENCE,
    )
    assert is_alive(state, NOW, TH) is False
    assert cause_of_death(state, NOW, TH) == "kill0_stale_and_silence"


def test_never_seen_frame_is_silent() -> None:
    state = make_state(last_frame_ts_monotonic=0.0)
    assert is_alive(state, NOW, TH) is False
    assert cause_of_death(state, NOW, TH) == "silence_timeout"
