from __future__ import annotations

from typing import Final

from orchestrator.transcript import Attempt, Context, TranscriptDocument, UnavailableContext

_BELL: Final = 0x07
_ESC: Final = 0x1B
_INTERMEDIATE_MIN: Final = 0x20
_INTERMEDIATE_MAX: Final = 0x2F
_CSI_FINAL_MIN: Final = 0x40
_CSI_FINAL_MAX: Final = 0x7E
_DCS: Final = 0x90
_CSI: Final = 0x9B
_STRING_TERMINATOR: Final = 0x9C
_OSC: Final = 0x9D
_CONTROL_STRINGS: Final = frozenset({_DCS, 0x98, 0x9E, 0x9F})


def _consume_csi(value: str, index: int) -> int:
    while index < len(value):
        codepoint = ord(value[index])
        index += 1
        if _CSI_FINAL_MIN <= codepoint <= _CSI_FINAL_MAX:
            break
    return index


def _consume_control_string(value: str, index: int, *, bell_terminated: bool) -> int:
    while index < len(value):
        codepoint = ord(value[index])
        if bell_terminated and codepoint == _BELL:
            return index + 1
        if codepoint == _STRING_TERMINATOR:
            return index + 1
        if codepoint == _ESC and index + 1 < len(value) and value[index + 1] == "\\":
            return index + 2
        index += 1
    return index


def _consume_escape(value: str, index: int) -> int:
    if index >= len(value):
        return index
    introducer = value[index]
    index += 1
    if introducer == "[":
        return _consume_csi(value, index)
    if introducer == "]":
        return _consume_control_string(value, index, bell_terminated=True)
    if introducer in "PX^_":
        return _consume_control_string(value, index, bell_terminated=False)
    if not _INTERMEDIATE_MIN <= ord(introducer) <= _INTERMEDIATE_MAX:
        return index
    while index < len(value) and _INTERMEDIATE_MIN <= ord(value[index]) <= _INTERMEDIATE_MAX:
        index += 1
    return index + 1 if index < len(value) else index


def sanitize_text(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        codepoint = ord(character)
        index += 1
        if codepoint == _ESC:
            index = _consume_escape(value, index)
        elif codepoint == _CSI:
            index = _consume_csi(value, index)
        elif codepoint == _OSC:
            index = _consume_control_string(value, index, bell_terminated=True)
        elif codepoint in _CONTROL_STRINGS:
            index = _consume_control_string(value, index, bell_terminated=False)
        elif character in "\n\t" or character.isprintable():
            output.append(character)
    return "".join(output)


def _inline(value: str) -> str:
    return sanitize_text(value).replace("\n", "\n  ")


def _content(value: str) -> str:
    return f"  {_inline(value)}"


def _value(value: str | int | float | bool | None) -> str:
    return "unavailable" if value is None else _inline(str(value))


def _context_lines(attempt: Attempt) -> list[str]:
    lines = ["CONTEXT"]
    match attempt.context:
        case Context(messages=messages):
            for message in messages:
                lines.extend((f"[{_inline(message.role)}]", _content(message.content)))
        case UnavailableContext(reason=reason):
            label = _inline(reason.replace("_", " "))
            lines.append(f"[context unavailable: {label}]")
    return lines


def _outcome_lines(attempt: Attempt) -> list[str]:
    match attempt.outcome.kind:
        case "reply":
            return [
                "REPLY",
                _content(attempt.outcome.content),
                f"PARSE ok={attempt.outcome.parse_ok} parser={_inline(attempt.outcome.parser)} "
                f"error={_value(attempt.outcome.parse_error)}",
            ]
        case "failure":
            failure = attempt.outcome
            return [
                f"FAILURE category={_inline(failure.category)} "
                f"finish_reason={_value(failure.finish_reason)} "
                f"error_class={_value(failure.error_class)} "
                f"status_code={_value(failure.status_code)}",
                _content(failure.error_text),
                "PARSE unavailable",
            ]
        case "unavailable":
            label = "legacy log" if attempt.outcome.reason == "legacy_log" else "not recorded"
            return [f"[provider outcome unavailable: {label}]", "PARSE unavailable"]


def _command_lines(attempt: Attempt) -> list[str]:
    lines: list[str] = []
    for command in attempt.commands:
        lines.extend(
            (
                f"COMMAND request_id={_inline(command.request_id)}",
                _content(command.keystrokes),
            )
        )
        if command.result is None:
            lines.append("RESULT unavailable")
        else:
            result = command.result
            lines.extend(
                (
                    f"RESULT exit_status={_value(result.exit_status)} "
                    f"duration_s={result.duration_s} "
                    f"truncated_bytes={result.truncated_bytes}",
                    _content(result.terminal_output),
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
        f"granted={_value(reservation.granted if reservation else None)} "
        f"reservation_reason={_value(reservation.reason if reservation else None)}",
    ]


def render_text(document: TranscriptDocument) -> str:
    run = document.run
    run_bits = [f"RUN {_inline(run.match_id)}"]
    if run.run_id is not None:
        run_bits.append(f"run_id={_inline(run.run_id)}")
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
                    ",".join(_inline(model) for model in request.fallback_models)
                    if request.fallback_models
                    else None
                )
                lines.append(
                    f"ATTEMPT {attempt.attempt} request_id={_inline(request.request_id)} "
                    f"model={_inline(request.model)}"
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
                f"HARNESS EXIT reason={_inline(exit_frame.reason)} code={_value(exit_frame.code)} "
                f"last_turn={exit_frame.last_turn}"
            )
    return "\n".join(lines) + "\n"
