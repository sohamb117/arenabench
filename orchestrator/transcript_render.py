from __future__ import annotations

import re
from typing import Final

from orchestrator.transcript import Attempt, TranscriptDocument

_ANSI_ESCAPE: Final = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _value(value: str | int | float | bool | None) -> str:
    return "unavailable" if value is None else str(value)


def _plain(value: str) -> str:
    return _ANSI_ESCAPE.sub("", value)


def _context_lines(attempt: Attempt) -> list[str]:
    lines = ["CONTEXT"]
    match attempt.context.kind:
        case "context":
            for message in attempt.context.messages:
                lines.extend((f"[{message.role}]", _plain(message.content)))
        case "unavailable":
            label = "legacy log" if attempt.context.reason == "legacy_log" else "not recorded"
            lines.append(f"[context unavailable: {label}]")
    return lines


def _outcome_lines(attempt: Attempt) -> list[str]:
    match attempt.outcome.kind:
        case "reply":
            return [
                "REPLY",
                _plain(attempt.outcome.content),
                f"PARSE ok={attempt.outcome.parse_ok} parser={attempt.outcome.parser} "
                f"error={_value(attempt.outcome.parse_error)}",
            ]
        case "failure":
            failure = attempt.outcome
            return [
                f"FAILURE category={failure.category} "
                f"finish_reason={_value(failure.finish_reason)} "
                f"error_class={_value(failure.error_class)} "
                f"status_code={_value(failure.status_code)}",
                _plain(failure.error_text),
                "PARSE unavailable",
            ]
        case "unavailable":
            label = "legacy log" if attempt.outcome.reason == "legacy_log" else "not recorded"
            return [f"[provider outcome unavailable: {label}]", "PARSE unavailable"]


def _command_lines(attempt: Attempt) -> list[str]:
    lines: list[str] = []
    for command in attempt.commands:
        lines.extend((f"COMMAND request_id={command.request_id}", _plain(command.keystrokes)))
        if command.result is None:
            lines.append("RESULT unavailable")
        else:
            result = command.result
            lines.extend(
                (
                    f"RESULT exit_status={_value(result.exit_status)} "
                    f"duration_s={result.duration_s} "
                    f"truncated_bytes={result.truncated_bytes}",
                    _plain(result.terminal_output),
                )
            )
    return lines


def _accounting_lines(attempt: Attempt) -> list[str]:
    usage = attempt.usage
    reservation = attempt.reservation
    reserved_nano_usd = reservation.reserved_nano_usd if reservation else None
    reserved_usd = reserved_nano_usd / 1_000_000_000 if reserved_nano_usd is not None else None
    return [
        "USAGE "
        f"prompt={_value(usage.prompt_tokens if usage else None)} "
        f"completion={_value(usage.completion_tokens if usage else None)} "
        f"total={_value(usage.total_tokens if usage else None)}",
        f"LATENCY seconds={_value(attempt.latency_s)}",
        f"COST provider_usd={_value(attempt.cost_usd)}",
        "RESERVED "
        f"usd={_value(reserved_usd)} nano_usd={_value(reserved_nano_usd)} "
        f"granted={_value(reservation.granted if reservation else None)}",
    ]


def render_text(document: TranscriptDocument) -> str:
    run = document.run
    run_bits = [f"RUN {run.match_id}"]
    if run.run_id is not None:
        run_bits.append(f"run_id={run.run_id}")
    if run.legacy_run is not None:
        run_bits.append(f"legacy_run={run.legacy_run}/{(run.legacy_run_count or 1) - 1}")
    lines = [" ".join(run_bits)]
    for agent in document.agents:
        lines.append(f"AGENT {agent.slot}")
        for turn in agent.turns:
            lines.append(f"TURN {turn.turn}")
            for attempt in turn.attempts:
                request = attempt.request
                fallback_models = (
                    ",".join(request.fallback_models) if request.fallback_models else None
                )
                lines.append(
                    f"ATTEMPT {attempt.attempt} request_id={request.request_id} "
                    f"model={request.model}"
                )
                lines.append(
                    f"REQUEST messages={request.messages_count} chars={request.prompt_chars} "
                    f"temperature={request.temperature} "
                    f"prompt_tokens={_value(request.prompt_tokens)} "
                    f"max_output_tokens={_value(request.max_output_tokens)} "
                    f"fallback_models={_value(fallback_models)}"
                )
                lines.extend(_context_lines(attempt))
                lines.extend(_outcome_lines(attempt))
                lines.extend(_command_lines(attempt))
                lines.extend(_accounting_lines(attempt))
            if turn.summary is not None:
                summary = turn.summary
                lines.append(
                    f"TURN SUMMARY actions={summary.action_count} "
                    f"free_tokens={summary.free_tokens} "
                    f"summarized={summary.summarized} history_chars={summary.history_chars}"
                )
        if agent.harness_exit is not None:
            exit_frame = agent.harness_exit
            lines.append(
                f"HARNESS EXIT reason={exit_frame.reason} code={_value(exit_frame.code)} "
                f"last_turn={exit_frame.last_turn}"
            )
    return "\n".join(lines) + "\n"
