from dataclasses import dataclass

from common.ids import AgentSlot


@dataclass(frozen=True, slots=True)
class AgentLivenessState:
    slot: AgentSlot
    vsock_connected: bool
    last_frame_ts_monotonic: float
    llm_call_start_ts_monotonic: float | None
    kill0_alive: bool
    kill0_ts_monotonic: float


@dataclass(frozen=True, slots=True)
class LivenessThresholds:
    silence_threshold_s: float = 5.0
    llm_max_s: float = 300.0
    kill0_max_age_s: float = 5.0


def _silence_violation(
    state: AgentLivenessState, now_monotonic: float, th: LivenessThresholds
) -> bool:
    if (
        state.llm_call_start_ts_monotonic is not None
        and (now_monotonic - state.llm_call_start_ts_monotonic) < th.llm_max_s
    ):
        return False
    return (now_monotonic - state.last_frame_ts_monotonic) >= th.silence_threshold_s


def is_alive(state: AgentLivenessState, now_monotonic: float, th: LivenessThresholds) -> bool:
    """Plan §7 dual-signal formula. PURE — no I/O, no time access."""
    if not state.vsock_connected:
        return False
    if not state.kill0_alive:
        return False
    if (now_monotonic - state.kill0_ts_monotonic) > th.kill0_max_age_s:
        return False
    return not _silence_violation(state, now_monotonic, th)


def cause_of_death(state: AgentLivenessState, now_monotonic: float, th: LivenessThresholds) -> str:
    """One of: 'vsock_disconnect', 'kill0_dead', 'silence_timeout',
    'kill0_stale_and_silence', 'alive'. Returns 'alive' iff is_alive() is True."""
    if not state.vsock_connected:
        return "vsock_disconnect"

    silence = _silence_violation(state, now_monotonic, th)
    kill0_stale = (now_monotonic - state.kill0_ts_monotonic) > th.kill0_max_age_s

    if not state.kill0_alive or kill0_stale:
        if kill0_stale and silence:
            return "kill0_stale_and_silence"
        return "kill0_dead"

    if silence:
        return "silence_timeout"

    return "alive"
