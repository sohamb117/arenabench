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

    §3.A4 (locked): stdout-silence AND `kill -0` failure are BOTH required
    for process-level death. Strict interpretation:
    - vsock_disconnect alone IS fatal (transport-level, distinct from §3.A4
      process-liveness; required for R2 SSH-drop detection per §14.9).
    - silence alone is NOT fatal (quiet but running agent).
    - kill0_alive=False alone is NOT fatal — need silence corroboration to
      avoid acting on a racy probe response while frames are still flowing.
    - kill0_stale alone is NOT fatal (probe missed its window, agent may
      still be alive); needs silence to corroborate.
    - silence AND (kill0_alive=False OR kill0_stale) → dead.
    """
    if not state.vsock_connected:
        return False
    silence = _silence_violation(state, now_monotonic, th)
    kill0_failed = (not state.kill0_alive) or (
        (now_monotonic - state.kill0_ts_monotonic) > th.kill0_max_age_s
    )
    return not (silence and kill0_failed)


def cause_of_death(state: AgentLivenessState, now_monotonic: float, th: LivenessThresholds) -> str:
    """Combined-signal cause string. Returns 'alive' if §3.A4 says alive.

    Token grammar (joined with '+'):
      'vsock_disconnect'         — explicit transport close
      'kill0_dead'               — kill0 probe returned alive=false
      'silence_timeout'          — appended when silence corroborates a
                                   transport signal
      'kill0_stale_and_silence'  — corroborated dual failure when no
                                   transport signal fired
      'alive'                    — §3.A4 says agent is alive

    Plan §9 S11 binary observable expects 'vsock_disconnect+kill0_dead'
    when both signals fail at once.
    """
    if is_alive(state, now_monotonic, th):
        return "alive"
    transport_signals: list[str] = []
    if not state.vsock_connected:
        transport_signals.append("vsock_disconnect")
    if not state.kill0_alive:
        transport_signals.append("kill0_dead")
    silence = _silence_violation(state, now_monotonic, th)
    if transport_signals:
        if silence:
            transport_signals.append("silence_timeout")
        return "+".join(transport_signals)
    return "kill0_stale_and_silence"
