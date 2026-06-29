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
    """Dual-signal liveness per plan §3.A4 and §7.

    - vsock_connected=False is always fatal (explicit disconnect).
    - kill0_alive=False (explicit dead response) is fatal.
    - silence_timeout is fatal on its own.
    - kill0_stale ALONE is not fatal (probe didn't respond in time, but
      frames are still flowing); only when paired with silence does it
      become a corroborated death. Avoids a transient 5s guest-probe
      stall killing an otherwise-live agent.
    """
    if not state.vsock_connected:
        return False
    if not state.kill0_alive:
        return False
    return not _silence_violation(state, now_monotonic, th)


def cause_of_death(state: AgentLivenessState, now_monotonic: float, th: LivenessThresholds) -> str:
    """One of: 'vsock_disconnect', 'kill0_dead', 'silence_timeout',
    'kill0_stale_and_silence', 'alive'. Returns 'alive' iff is_alive() is True."""
    if not state.vsock_connected:
        return "vsock_disconnect"

    silence = _silence_violation(state, now_monotonic, th)
    kill0_stale = (now_monotonic - state.kill0_ts_monotonic) > th.kill0_max_age_s

    if not state.kill0_alive:
        return "kill0_dead"

    if silence and kill0_stale:
        return "kill0_stale_and_silence"

    if silence:
        return "silence_timeout"

    return "alive"
