"""JSONL protocol envelopes for arena transport.

The wire format keeps `kind` at the envelope level for readability, while the
payload remains a discriminated union so Pydantic validates the active frame.
"""

import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True)
_RAW_ENVELOPE_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])


class BashCommand(BaseModel):
    model_config = _MODEL_CONFIG

    keystrokes: str
    duration_sec: float
    is_blocking: bool


class PidAnnounce(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["pid_announce"] = "pid_announce"
    pid: int
    user: str
    uid: int
    hostname: str
    parser: Literal["json", "xml"]
    model: str


class BashRequest(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["bash_request"] = "bash_request"
    turn: int
    request_id: str
    commands: list[BashCommand]


class BashResult(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["bash_result"] = "bash_result"
    turn: int
    request_id: str
    terminal_output: str
    truncated_bytes: int = 0
    exit_status: int | None = None
    duration_s: float


class LlmRequest(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["llm_request"] = "llm_request"
    turn: int
    request_id: str
    model: str
    messages_count: int
    prompt_chars: int
    temperature: float


class LlmResponse(BaseModel):
    model_config = _MODEL_CONFIG

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


class HeartbeatInjected(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["heartbeat_injected"] = "heartbeat_injected"
    turn: int
    elapsed_s: float
    payload: str


class TurnSummary(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["turn_summary"] = "turn_summary"
    turn: int
    action_count: int
    free_tokens: int
    summarized: bool
    history_chars: int


class HarnessExit(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["harness_exit"] = "harness_exit"
    reason: Literal["clean", "crash", "llm_fatal", "signal", "shutdown_received"]
    code: int | None = None
    last_turn: int


class HeartbeatTick(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["heartbeat_tick"] = "heartbeat_tick"
    elapsed_s: float
    turn_hint: int


class Shutdown(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["shutdown"] = "shutdown"
    reason: str


class Kill0(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["kill0"] = "kill0"
    request_id: str
    pid: int


class Kill0Response(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["kill0_response"] = "kill0_response"
    request_id: str
    pid: int
    alive: bool


class ProcList(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["proc_list"] = "proc_list"
    request_id: str
    user: str


class ProcListResponse(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["proc_list_response"] = "proc_list_response"
    request_id: str
    user: str
    pids: list[int]


class BootAck(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["boot_ack"] = "boot_ack"
    request_id: str


class BootAckResponse(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["boot_ack_response"] = "boot_ack_response"
    request_id: str
    kernel: str
    uptime_s: float
    cid: int


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
    | BootAckResponse,
    Field(discriminator="kind"),
]


class Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal[1] = 1
    ts: datetime
    seq: int = Field(ge=0)
    src: str
    dst: str | None = None
    kind: str
    data: Frame

    @model_validator(mode="after")
    def _validate_kind(self) -> "Envelope":
        if self.kind != self.data.kind:
            raise ValueError("kind must match data.kind")
        return self


def parse_envelope(line: str) -> Envelope:
    raw: dict[str, object] = _RAW_ENVELOPE_ADAPTER.validate_json(line)
    data = raw.get("data")
    kind = raw.get("kind")
    if isinstance(kind, str) and isinstance(data, dict) and "kind" not in data:
        raw = {**raw, "data": {**data, "kind": kind}}
    return Envelope.model_validate(raw)


def serialize_envelope(env: Envelope) -> str:
    body = env.model_dump(mode="json", exclude={"data": {"kind"}})
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False)
