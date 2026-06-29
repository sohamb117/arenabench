from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Literal

from common import protocol as proto
from harness import llm, parser, shell
from harness.chat import Chat

Emit = Callable[[proto.Frame], None]


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
        content=result.content,
        parser=mode,
        parse_ok=parse_ok,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        cost_usd=result.cost_usd,
        latency_s=result.latency_s,
        error=error,
    )


def parse_or_record_error(
    result: llm.LlmCallResult,
    mode: Literal["json", "xml"],
    turn: int,
    request_id: str,
    emit: Emit,
) -> parser.ParsedResponse | None:
    try:
        parsed = parser.parse(result.content, mode=mode)
    except parser.ParseError as exc:
        emit(build_llm_response(result, mode, turn, request_id, False, str(exc)))
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
        chat.append_user(chat.truncate_terminal_output(result.terminal_output))


def chat_history_for_litellm(chat: Chat) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in chat.history]
