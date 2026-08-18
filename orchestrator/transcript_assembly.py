from __future__ import annotations

from dataclasses import dataclass

from orchestrator.transcript import (
    Attempt,
    Command,
    CommandResult,
    Context,
    Message,
    ProviderFailure,
    Reply,
    RequestMetadata,
    Reservation,
    Turn,
    TurnSummary,
    UnavailableContext,
    UnavailableOutcome,
    Usage,
)
from orchestrator.transcript_frames import (
    BashRequestFrame,
    BashResultFrame,
    ContextFrame,
    FailureFrame,
    RequestFrame,
    ResponseFrame,
    TurnSummaryFrame,
)


@dataclass(frozen=True, slots=True)
class AttemptKey:
    turn: int
    request_id: str
    attempt: int


@dataclass(frozen=True, slots=True)
class AssemblyInput:
    requests: dict[AttemptKey, RequestFrame]
    contexts: dict[AttemptKey, ContextFrame]
    failures: dict[AttemptKey, FailureFrame]
    responses: dict[AttemptKey, ResponseFrame]
    bash_requests: list[BashRequestFrame]
    bash_results: dict[str, BashResultFrame]
    summaries: dict[int, TurnSummaryFrame]
    reservations: dict[tuple[int, str, int], Reservation]
    slot: int
    legacy: bool
    turn_filter: int | None


def build_turns(source: AssemblyInput) -> tuple[Turn, ...]:
    keys = sorted(source.requests, key=lambda key: (key.turn, key.request_id, key.attempt))
    turns: list[Turn] = []
    for turn_number in sorted({key.turn for key in keys}):
        if source.turn_filter is not None and turn_number != source.turn_filter:
            continue
        turn_keys = [key for key in keys if key.turn == turn_number]
        command_attempt = max(key.attempt for key in turn_keys)
        attempts = tuple(
            _build_attempt(
                key,
                source.requests[key],
                source,
                include_commands=key.attempt == command_attempt,
            )
            for key in turn_keys
        )
        summary_frame = source.summaries.get(turn_number)
        summary = (
            None
            if summary_frame is None
            else TurnSummary(
                action_count=summary_frame.action_count,
                free_tokens=summary_frame.free_tokens,
                summarized=summary_frame.summarized,
                history_chars=summary_frame.history_chars,
            )
        )
        turns.append(Turn(turn=turn_number, attempts=attempts, summary=summary))
    return tuple(turns)


def _build_attempt(
    key: AttemptKey,
    request: RequestFrame,
    source: AssemblyInput,
    *,
    include_commands: bool,
) -> Attempt:
    context = source.contexts.get(key)
    failure = source.failures.get(key)
    response = source.responses.get(key)
    request_model = RequestMetadata(
        request_id=request.request_id,
        model=request.model,
        messages_count=request.messages_count,
        prompt_chars=request.prompt_chars,
        temperature=request.temperature,
        prompt_tokens=request.prompt_tokens,
        max_output_tokens=request.max_output_tokens,
        fallback_models=request.fallback_models,
        last_user_excerpt=request.last_user_excerpt,
    )
    context_model = (
        UnavailableContext(reason="legacy_log" if source.legacy else "not_recorded")
        if context is None
        else Context(
            messages=tuple(
                Message(role=message.role, content=message.content) for message in context.messages
            )
        )
    )
    usage: Usage | None = None
    latency_s: float | None = None
    cost_usd: float | None = None
    if response is not None:
        outcome = Reply(
            content=response.content,
            parser=response.parser,
            parse_ok=response.parse_ok,
            parsed=response.parsed,
            parse_error=response.error,
        )
        usage = Usage(
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            total_tokens=response.total_tokens,
        )
        latency_s = response.latency_s
        cost_usd = response.cost_usd
    elif failure is not None:
        outcome = ProviderFailure(
            category=failure.category,
            finish_reason=failure.finish_reason,
            error_text=failure.error_text,
            error_class=failure.error_class,
            status_code=failure.status_code,
        )
        usage = Usage(
            prompt_tokens=failure.prompt_tokens,
            completion_tokens=failure.completion_tokens,
            total_tokens=failure.total_tokens,
        )
        latency_s = failure.latency_s
    else:
        outcome = UnavailableOutcome(reason="legacy_log" if source.legacy else "not_recorded")
    commands: list[Command] = []
    if include_commands:
        for bash_request in source.bash_requests:
            if bash_request.turn != key.turn:
                continue
            result = source.bash_results.get(bash_request.request_id)
            result_model = (
                None
                if result is None
                else CommandResult(
                    terminal_output=result.terminal_output,
                    truncated_bytes=result.truncated_bytes,
                    exit_status=result.exit_status,
                    duration_s=result.duration_s,
                )
            )
            commands.extend(
                Command(
                    request_id=bash_request.request_id,
                    keystrokes=command.keystrokes,
                    duration_sec=command.duration_sec,
                    is_blocking=command.is_blocking,
                    result=result_model,
                )
                for command in bash_request.commands
            )
    return Attempt(
        attempt=key.attempt,
        request=request_model,
        context=context_model,
        outcome=outcome,
        commands=tuple(commands),
        usage=usage,
        latency_s=latency_s,
        cost_usd=cost_usd,
        reservation=source.reservations.get((source.slot, key.request_id, key.attempt)),
    )
