from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class TranscriptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TranscriptError(Exception):
    pass


class TranscriptFilters(TranscriptModel):
    agent: Annotated[int | None, Field(default=None, ge=0, le=15)]
    turn: Annotated[int | None, Field(default=None, ge=0)]
    legacy_run: Annotated[int | None, Field(default=None, ge=0)]


class TranscriptFormat(StrEnum):
    TEXT = "text"
    JSON = "json"


class RunInfo(TranscriptModel):
    match_id: str
    run_id: str | None = None
    started_at: datetime | None = None
    legacy: bool
    legacy_run: int | None = None
    legacy_run_count: int | None = None


class Message(TranscriptModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class Context(TranscriptModel):
    kind: Literal["context"] = "context"
    messages: tuple[Message, ...]


class UnavailableContext(TranscriptModel):
    kind: Literal["unavailable"] = "unavailable"
    reason: Literal["legacy_log", "not_recorded"]


class RequestMetadata(TranscriptModel):
    request_id: str
    model: str
    messages_count: int
    prompt_chars: int
    temperature: float
    prompt_tokens: int | None = None
    max_output_tokens: int | None = None
    fallback_models: tuple[str, ...] | None = None
    last_user_excerpt: str = ""


class Reply(TranscriptModel):
    kind: Literal["reply"] = "reply"
    content: str
    parser: Literal["json", "xml"]
    parse_ok: bool
    parsed: JsonValue | None = None
    parse_error: str | None = None


class ProviderFailure(TranscriptModel):
    kind: Literal["failure"] = "failure"
    category: Literal[
        "context_too_large", "provider_error", "provider_refusal", "reservation_error"
    ]
    finish_reason: str | None = None
    error_text: str
    error_class: str | None = None
    status_code: int | None = None


class UnavailableOutcome(TranscriptModel):
    kind: Literal["unavailable"] = "unavailable"
    reason: Literal["legacy_log", "not_recorded"]


class Usage(TranscriptModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class Reservation(TranscriptModel):
    granted: bool
    reason: str | None
    reserved_nano_usd: int


class CommandResult(TranscriptModel):
    terminal_output: str
    truncated_bytes: int
    exit_status: int | None
    duration_s: float


class Command(TranscriptModel):
    request_id: str
    keystrokes: str
    duration_sec: float
    is_blocking: bool
    result: CommandResult | None = None


class Attempt(TranscriptModel):
    attempt: int
    request: RequestMetadata
    context: Context | UnavailableContext
    outcome: Reply | ProviderFailure | UnavailableOutcome
    commands: tuple[Command, ...] = ()
    usage: Usage | None = None
    latency_s: float | None = None
    cost_usd: float | None = None
    reservation: Reservation | None = None


class TurnSummary(TranscriptModel):
    action_count: int
    free_tokens: int
    summarized: bool
    history_chars: int


class Turn(TranscriptModel):
    turn: int
    attempts: tuple[Attempt, ...]
    summary: TurnSummary | None = None


class HarnessExit(TranscriptModel):
    reason: str
    code: int | None
    last_turn: int


class Agent(TranscriptModel):
    slot: int
    turns: tuple[Turn, ...]
    harness_exit: HarnessExit | None = None


class TranscriptDocument(TranscriptModel):
    schema_version: Literal[1] = 1
    run: RunInfo
    agents: tuple[Agent, ...]


def read_transcript(
    match_dir: Path,
    *,
    agent: int | None = None,
    turn: int | None = None,
    legacy_run: int | None = None,
) -> TranscriptDocument:
    from orchestrator.transcript_reader import TranscriptReader  # noqa: PLC0415

    filters = TranscriptFilters(agent=agent, turn=turn, legacy_run=legacy_run)
    return TranscriptReader(match_dir, filters).read()
