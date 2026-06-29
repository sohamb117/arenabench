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

    §3.A4: stdout-silence AND `kill -0` failure are BOTH required for death.
    - vsock_connected=False is fatal alone (explicit close — distinct from
      silence; the R2 SSH transport drop must be detectable instantly).
    - kill0_alive=False is fatal alone (explicit dead-process signal).
    - silence_timeout alone is NOT fatal (quiet but-running agent); it is
      fatal only when corroborated by kill0_stale (probe also stopped
      responding within kill0_max_age_s).
    """
    if not state.vsock_connected:
        return False
    if not state.kill0_alive:
        return False
    silence = _silence_violation(state, now_monotonic, th)
    kill0_stale = (now_monotonic - state.kill0_ts_monotonic) > th.kill0_max_age_s
    return not (silence and kill0_stale)


def cause_of_death(state: AgentLivenessState, now_monotonic: float, th: LivenessThresholds) -> str:
    """Combined-signal cause string. Returns '+'-joined list of failing signals.

    Possible single tokens (joined with '+'):
      'vsock_disconnect'        — explicit close
      'kill0_dead'              — kill0 probe returned alive=false
      'kill0_stale_and_silence' — corroborated dual failure (§3.A4): probe
                                  stale AND no frames within silence threshold
      'alive'                   — agent is alive

    Plan §9 S11 binary observable expects the combined form
    'vsock_disconnect+kill0_dead' when both signals fail at once.
    """
    silence = _silence_violation(state, now_monotonic, th)
    kill0_stale = (now_monotonic - state.kill0_ts_monotonic) > th.kill0_max_age_s

    transport_signals: list[str] = []
    if not state.vsock_connected:
        transport_signals.append("vsock_disconnect")
    if not state.kill0_alive:
        transport_signals.append("kill0_dead")

    if transport_signals:
        if silence:
            transport_signals.append("silence_timeout")
        return "+".join(transport_signals)

    if silence and kill0_stale:
        return "kill0_stale_and_silence"

    return "alive"
