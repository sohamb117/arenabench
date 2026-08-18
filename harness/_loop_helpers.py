from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Literal, Protocol

from common import protocol as proto
from harness import attempt_logging, llm, parser, shell
from harness.chat import Chat

Emit = Callable[[proto.Frame], None]


class TurnState(Protocol):
    turn: int
    pending_complete: bool

    def emit(self, data: proto.Frame) -> None: ...


def build_llm_response(
    result: llm.LlmCallResult,
    mode: Literal["json", "xml"],
    turn: int,
    request_id: str,
    parse_ok: bool,
    error: str | None,
) -> proto.LlmResponse:
    return proto.LlmResponse(
        turn=turn,
        request_id=request_id,
        attempt=result.attempt,
        content=result.content,
        parser=mode,
        parse_ok=parse_ok,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        cost_usd=result.cost_usd,
        latency_s=result.latency_s,
        parse_error=attempt_logging.safe_error_text(error) if error is not None else None,
    )


def parse_or_record_error(
    result: llm.LlmCallResult,
    mode: Literal["json", "xml"],
    turn: int,
    request_id: str,
    emit: Emit,
    chat: Chat,
) -> parser.ParsedResponse | None:
    try:
        parsed = parser.parse(result.content, mode=mode)
    except parser.ParseError as exc:
        emit(build_llm_response(result, mode, turn, request_id, False, result.error or str(exc)))
        chat.append_assistant(result.content)
        chat.append_user(
            f"Your previous response did not parse as {mode}: {exc}. "
            f"Reply with a valid {mode} object: "
            '{"analysis": "...", "plan": "...", "commands": [...], "task_complete": false}'
        )
        return None
    emit(build_llm_response(result, mode, turn, request_id, True, None))
    return parsed


def run_commands(
    parsed: parser.ParsedResponse,
    turn: int,
    chat: Chat,
    tmux: shell.TmuxShell,
    emit: Emit,
    id_bytes: int,
) -> None:
    if not parsed.commands:
        return
    outputs: list[str] = []
    for index, command in enumerate(parsed.commands):
        request_id = f"bash-{turn}-{index}-{uuid.uuid4().hex[:id_bytes]}"
        emit(proto.BashRequest(turn=turn, request_id=request_id, commands=[command]))
        result = tmux.run(
            command.keystrokes,
            duration_sec=command.duration_sec,
            is_blocking=command.is_blocking,
        )
        emit(
            proto.BashResult(
                turn=turn,
                request_id=request_id,
                terminal_output=result.terminal_output,
                truncated_bytes=result.truncated_bytes,
                exit_status=result.exit_status,
                duration_s=result.duration_s,
            )
        )
        outputs.append(f"$ {command.keystrokes}\n{result.terminal_output}")
    chat.append_user(chat.truncate_terminal_output("\n".join(outputs)))


def chat_history_for_litellm(chat: Chat) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in chat.history]


def finish_turn(state: TurnState, chat: Chat, parsed: parser.ParsedResponse | None) -> bool:
    summarized = chat.should_summarize()
    state.emit(
        proto.TurnSummary(
            turn=state.turn,
            action_count=len(parsed.commands) if parsed is not None else 0,
            free_tokens=chat.free_tokens,
            summarized=summarized,
            history_chars=sum(len(message.content) for message in chat.history),
        )
    )
    complete = parsed is not None and parsed.task_complete
    if complete and state.pending_complete:
        return True
    state.pending_complete = complete
    if summarized:
        chat.summarize(f"[context summarized at turn {state.turn}]")
    state.turn += 1
    return False
