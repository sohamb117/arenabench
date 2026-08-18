from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from common.context_protocol import LlmContextChunk as ContextChunkFrame


class FrameModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Envelope(FrameModel):
    ts: datetime
    src: str
    dst: str | None = None
    kind: str
    data: dict[str, JsonValue]


class MessageFrame(FrameModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ContextFrame(FrameModel):
    turn: Annotated[int, Field(ge=0)]
    request_id: str
    attempt: Annotated[int, Field(ge=0)] = 0
    messages: tuple[MessageFrame, ...]


class FailureFrame(FrameModel):
    turn: int
    request_id: str
    attempt: int = 0
    category: Literal[
        "context_logging_error",
        "context_too_large",
        "provider_error",
        "provider_refusal",
        "reservation_error",
    ]
    finish_reason: str | None = None
    error_text: str
    error_class: str | None = None
    status_code: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    latency_s: float | None = None


class RequestFrame(FrameModel):
    turn: int
    request_id: str
    attempt: int = 0
    model: str
    messages_count: int
    prompt_chars: int
    temperature: float
    prompt_tokens: int | None = None
    max_output_tokens: int | None = None
    fallback_models: tuple[str, ...] | None = None
    last_user_excerpt: str = ""


class ResponseFrame(FrameModel):
    turn: int
    request_id: str
    attempt: int = 0
    content: str
    parser: Literal["json", "xml"]
    parse_ok: bool
    parsed: JsonValue | None = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None = None
    latency_s: float
    error: str | None = None


class BashCommandFrame(FrameModel):
    keystrokes: str
    duration_sec: float
    is_blocking: bool


class BashRequestFrame(FrameModel):
    turn: int
    request_id: str
    commands: tuple[BashCommandFrame, ...]


class BashResultFrame(FrameModel):
    turn: int
    request_id: str
    terminal_output: str
    truncated_bytes: int = 0
    exit_status: int | None = None
    duration_s: float


class TurnSummaryFrame(FrameModel):
    turn: int
    action_count: int
    free_tokens: int
    summarized: bool
    history_chars: int


class HarnessExitFrame(FrameModel):
    reason: str
    code: int | None = None
    last_turn: int


class ReservationFrame(FrameModel):
    request_id: str
    attempt: int = 0
    granted: bool
    reason: str | None = None
    reserved_nano_usd: int = 0


class StateChangeFrame(FrameModel):
    from_state: str
    to_state: str


type AgentFrame = (
    ContextFrame
    | ContextChunkFrame
    | FailureFrame
    | RequestFrame
    | ResponseFrame
    | BashRequestFrame
    | BashResultFrame
    | TurnSummaryFrame
    | HarnessExitFrame
)
