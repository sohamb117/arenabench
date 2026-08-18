from __future__ import annotations

import json
import time

from orchestrator.transcript import (
    Agent,
    Attempt,
    Command,
    CommandResult,
    Context,
    HarnessExit,
    Message,
    ProviderFailure,
    Reply,
    RequestMetadata,
    Reservation,
    RunInfo,
    TranscriptDocument,
    Turn,
)
from orchestrator.transcript_render import render_text, sanitize_text

_CONTROLS = "\x1bc\x1b]0;owned\x07\x1bPpayload\x1b\\\x9b31m\r\b\x07\x7f\x85"
_FORGED = "safe\nRUN forged\nHARNESS EXIT reason=forged\nRESULT forged"
_ATTACK = f"{_CONTROLS}{_FORGED}"


def _document(outcome: Reply | ProviderFailure) -> TranscriptDocument:
    attempt = Attempt(
        attempt=0,
        request=RequestMetadata(
            request_id=f"request-{_ATTACK}",
            model=f"model-{_ATTACK}",
            messages_count=1,
            prompt_chars=1,
            temperature=0.0,
            fallback_models=(f"fallback-{_ATTACK}",),
            last_user_excerpt=_ATTACK,
        ),
        context=Context(messages=(Message(role="user", content=_ATTACK),)),
        outcome=outcome,
        commands=(
            Command(
                request_id=f"command-{_ATTACK}",
                keystrokes=_ATTACK,
                duration_sec=0.1,
                is_blocking=True,
                result=CommandResult(
                    terminal_output=_ATTACK,
                    truncated_bytes=0,
                    exit_status=0,
                    duration_s=0.1,
                ),
            ),
        ),
        reservation=Reservation(
            granted=False,
            reason=_ATTACK,
            reserved_nano_usd=0,
        ),
    )
    return TranscriptDocument(
        run=RunInfo(
            match_id="safe-match",
            run_id="20260817T120000000000000Z-abcdef123456",
            legacy=False,
        ),
        agents=(
            Agent(
                slot=0,
                turns=(Turn(turn=0, attempts=(attempt,)),),
                harness_exit=HarnessExit(reason=_ATTACK, code=1, last_turn=0),
            ),
        ),
    )


def _assert_safe_text(rendered: str) -> None:
    assert all(character in "\n\t" or character.isprintable() for character in rendered)
    assert "owned" not in rendered
    assert "payload" not in rendered
    assert "\x1b" not in rendered
    assert "\x9b" not in rendered
    assert "\r" not in rendered
    assert "\b" not in rendered
    assert "\x07" not in rendered
    assert "\x7f" not in rendered
    assert "\x85" not in rendered
    for forged in ("RUN forged", "HARNESS EXIT reason=forged", "RESULT forged"):
        assert f"\n  {forged}" in rendered
        assert f"\n{forged}" not in rendered


def test_text_renderer_sanitizes_reply_and_all_rendered_string_fields() -> None:
    document = _document(
        Reply(
            content=_ATTACK,
            parser="json",
            parse_ok=False,
            parse_error=_ATTACK,
        )
    )

    rendered = render_text(document)

    _assert_safe_text(rendered)
    assert "reservation_reason=" in rendered


def test_text_renderer_sanitizes_provider_failure_fields() -> None:
    document = _document(
        ProviderFailure(
            category="provider_error",
            finish_reason=_ATTACK,
            error_text=_ATTACK,
            error_class=_ATTACK,
        )
    )

    rendered = render_text(document)

    _assert_safe_text(rendered)


def test_text_sanitizer_handles_large_unterminated_sequence_in_linear_time() -> None:
    payload = "\x1b]" + "x" * 1_000_000
    started = time.monotonic()

    rendered = sanitize_text(payload)

    assert rendered == ""
    assert time.monotonic() - started < 1.0


def test_json_renderer_input_remains_exact() -> None:
    document = _document(
        Reply(
            content=_ATTACK,
            parser="json",
            parse_ok=False,
            parse_error=_ATTACK,
        )
    )

    serialized = document.model_dump_json()
    restored = TranscriptDocument.model_validate_json(serialized)

    attempt = restored.agents[0].turns[0].attempts[0]
    assert attempt.context.kind == "context"
    assert attempt.context.messages[0].content == _ATTACK
    assert attempt.outcome.kind == "reply"
    assert attempt.outcome.content == _ATTACK
    assert attempt.outcome.parse_error == _ATTACK
    assert attempt.commands[0].result is not None
    assert attempt.commands[0].result.terminal_output == _ATTACK
    assert json.loads(serialized)
