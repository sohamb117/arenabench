"""JSONL protocol envelopes for arena transport.

Wire format keeps `kind` at envelope level for readability while the payload
remains a discriminated union so Pydantic validates the active frame. All
timestamps MUST be tz-aware UTC. Frame size is capped at MAX_FRAME_BYTES
(plan §6.3); transport layer is responsible for per-source seq monotonicity.
"""

import json
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

MAX_FRAME_BYTES = 64 * 1024
_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True)
_RAW_ENVELOPE_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
_ORCH_TO_HARNESS_KINDS: frozenset[str] = frozenset({"heartbeat_tick", "shutdown"})


class _Frame(BaseModel):
    model_config = _MODEL_CONFIG


class BashCommand(_Frame):
    keystrokes: str
    duration_sec: float
    is_blocking: bool


class PidAnnounce(_Frame):
    kind: Literal["pid_announce"] = "pid_announce"
    pid: int
    user: str
    uid: int
    hostname: str
    parser: Literal["json", "xml"]
    model: str


class BashRequest(_Frame):
    kind: Literal["bash_request"] = "bash_request"
    turn: int
    request_id: str
    commands: list[BashCommand]


class BashResult(_Frame):
    kind: Literal["bash_result"] = "bash_result"
    turn: int
    request_id: str
    terminal_output: str
    truncated_bytes: int = 0
    exit_status: int | None = None
    duration_s: float


class LlmRequest(_Frame):
    kind: Literal["llm_request"] = "llm_request"
    turn: int
    request_id: str
    model: str
    messages_count: int
    prompt_chars: int
    temperature: float
    last_user_excerpt: str = ""
    attempt: int = 0


class LlmResponse(_Frame):
    kind: Literal["llm_response"] = "llm_response"
    turn: int
    request_id: str
    content: str
    parser: Literal["json", "xml"]
    parse_ok: bool
    parsed: dict[str, object] | None = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None = None
    latency_s: float
    error: str | None = None


class HeartbeatInjected(_Frame):
    kind: Literal["heartbeat_injected"] = "heartbeat_injected"
    turn: int
    elapsed_s: float
    payload: str


class TurnSummary(_Frame):
    kind: Literal["turn_summary"] = "turn_summary"
    turn: int
    action_count: int
    free_tokens: int
    summarized: bool
    history_chars: int


class HarnessExit(_Frame):
    kind: Literal["harness_exit"] = "harness_exit"
    reason: Literal["clean", "crash", "llm_fatal", "signal", "shutdown_received"]
    code: int | None = None
    last_turn: int


class HeartbeatTick(_Frame):
    kind: Literal["heartbeat_tick"] = "heartbeat_tick"
    elapsed_s: float
    turn_hint: int


class Shutdown(_Frame):
    kind: Literal["shutdown"] = "shutdown"
    reason: str


class Kill0(_Frame):
    kind: Literal["kill0"] = "kill0"
    request_id: str
    pid: int


class Kill0Response(_Frame):
    kind: Literal["kill0_response"] = "kill0_response"
    request_id: str
    pid: int
    alive: bool


class ProcList(_Frame):
    kind: Literal["proc_list"] = "proc_list"
    request_id: str
    user: str


class ProcListResponse(_Frame):
    kind: Literal["proc_list_response"] = "proc_list_response"
    request_id: str
    user: str
    pids: list[int]


class BootAck(_Frame):
    kind: Literal["boot_ack"] = "boot_ack"
    request_id: str


class BootAckResponse(_Frame):
    kind: Literal["boot_ack_response"] = "boot_ack_response"
    request_id: str
    kernel: str
    uptime_s: float
    cid: int


class HarnessDead(_Frame):
    kind: Literal["harness_dead"] = "harness_dead"
    slot: int
    cause: str


class MatchStateChange(_Frame):
    kind: Literal["match_state_change"] = "match_state_change"
    from_state: str
    to_state: str
    reason: str


class MatchTerminated(_Frame):
    kind: Literal["match_terminated"] = "match_terminated"
    result: Literal["victory", "draw", "timeout", "error"]
    winner: int | None = None
    cause: str


Frame = Annotated[
    PidAnnounce
    | BashRequest
    | BashResult
    | LlmRequest
    | LlmResponse
    | HeartbeatInjected
    | TurnSummary
    | HarnessExit
    | HeartbeatTick
    | Shutdown
    | Kill0
    | Kill0Response
    | ProcList
    | ProcListResponse
    | BootAck
    | BootAckResponse
    | HarnessDead
    | MatchStateChange
    | MatchTerminated,
    Field(discriminator="kind"),
]


class Envelope(_Frame):
    v: Literal[1] = 1
    ts: datetime
    seq: int = Field(ge=0)
    src: str
    dst: str | None = None
    kind: str
    data: Frame

    @field_validator("ts")
    @classmethod
    def _ts_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() != timedelta(0):
            raise ValueError("ts must be timezone-aware UTC")
        return v

    @model_validator(mode="after")
    def _validate_envelope(self) -> "Envelope":
        if self.kind != self.data.kind:
            raise ValueError("kind must match data.kind")
        if self.kind in _ORCH_TO_HARNESS_KINDS and self.dst is None:
            raise ValueError(f"dst is required for kind={self.kind}")
        return self


def parse_envelope(line: str) -> Envelope:
    if len(line.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ValueError(f"frame exceeds {MAX_FRAME_BYTES} bytes")
    raw: dict[str, object] = _RAW_ENVELOPE_ADAPTER.validate_json(line)
    data = raw.get("data")
    kind = raw.get("kind")
    if isinstance(kind, str) and isinstance(data, dict) and "kind" not in data:
        raw = {**raw, "data": {**data, "kind": kind}}
    return Envelope.model_validate(raw)


def serialize_envelope(env: Envelope) -> str:
    body = env.model_dump(mode="json", exclude={"data": {"kind"}})
    out = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    if len(out.encode("utf-8")) > MAX_FRAME_BYTES:
        raise ValueError(f"serialized frame exceeds {MAX_FRAME_BYTES} bytes")
    return out
