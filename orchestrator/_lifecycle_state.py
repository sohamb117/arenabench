from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field

from common.ids import AgentSlot
from common.protocol import Envelope
from orchestrator.heartbeat_scheduler import AgentSchedulerState
from orchestrator.liveness import AgentLivenessState, LivenessThresholds
from orchestrator.logger import MatchLogger
from orchestrator.match_config import MatchConfig
from orchestrator.vsock_server import VsockServer


class VsockServerLike(Protocol):
    def recv_frame(self, port: int, timeout_s: float | None) -> Envelope | None: ...
    def send_frame(self, port: int, env: Envelope) -> None: ...
    def is_open(self, port: int) -> bool: ...


class MatchOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    result: Literal["victory", "draw", "timeout", "error"]
    winner: Annotated[int, Field(ge=0, le=15)] | None
    cause: Annotated[str, Field(min_length=1)]
    final_state: Literal["DONE"]
    alive_at_timeout: list[Annotated[int, Field(ge=0, le=15)]] | None = None
    total_duration_s: Annotated[float, Field(ge=0.0)] | None = None


def build_outcome(
    result: str,
    winner_slot: int | None,
    cause: str,
    *,
    alive_at_timeout: list[int] | None = None,
    total_duration_s: float | None = None,
) -> MatchOutcome:
    """Construct a validated MatchOutcome. Extracted so lifecycle.run_match
    stays under the 250 LOC cap; the cast + multi-line kwargs live here.
    """
    res_lit = cast(Literal["victory", "draw", "timeout", "error"], result)
    return MatchOutcome(
        result=res_lit,
        winner=winner_slot,
        cause=cause,
        final_state="DONE",
        alive_at_timeout=alive_at_timeout,
        total_duration_s=total_duration_s,
    )


@dataclass(frozen=True, slots=True)
class MatchContext:
    match_config: MatchConfig
    vsock_server: VsockServer | VsockServerLike
    guest_probe_port: int
    agent_ports: dict[int, int]
    logger: MatchLogger
    clock: Callable[[], float]
    liveness: LivenessThresholds
    poll_interval_s: float = 1.0


@dataclass
class AgentState:
    slot: int
    vsock_connected: bool = True
    last_frame_ts: float = 0.0
    last_heartbeat_ts: float = 0.0
    llm_call_start_ts: float | None = None
    kill0_alive: bool = True
    kill0_ts: float = 0.0
    pid: int | None = None
    dead_emitted: bool = False

    def to_liveness(self) -> AgentLivenessState:
        return AgentLivenessState(
            slot=cast(AgentSlot, self.slot),
            vsock_connected=self.vsock_connected,
            last_frame_ts_monotonic=self.last_frame_ts,
            llm_call_start_ts_monotonic=self.llm_call_start_ts,
            kill0_alive=self.kill0_alive,
            kill0_ts_monotonic=self.kill0_ts,
        )

    def to_scheduler(self) -> AgentSchedulerState:
        return AgentSchedulerState(
            slot=cast(AgentSlot, self.slot),
            last_activity_ts_monotonic=self.last_frame_ts,
            last_heartbeat_ts_monotonic=self.last_heartbeat_ts,
            llm_call_in_flight=self.llm_call_start_ts is not None,
        )
