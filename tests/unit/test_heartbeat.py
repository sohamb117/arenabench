from __future__ import annotations

import pytest

from common.errors import LifecycleError
from harness.chat import Chat
from harness.heartbeat import format_heartbeat, inject

ELAPSED_ROUNDING_INPUT = 120.7
ELAPSED_ROUNDING_OUTPUT = 121
TURN_COUNT = 3
ZERO_ELAPSED = 0.0
ZERO_TURN = 0


def _chat(roles: list[str]) -> Chat:
    chat = Chat(
        initial_user_prompt="initial prompt",
        model="gpt-4o",
        max_context_tokens=1_000_000,
    )
    if roles == ["assistant"]:
        chat.append_assistant("assistant message")
        return chat

    if roles == ["assistant", "user"]:
        chat.append_assistant("assistant message")
        chat.append_user("user message")
        return chat

    raise ValueError(f"unsupported role sequence: {roles}")
    return chat


def test_format_heartbeat_rounds_elapsed_seconds() -> None:
    assert format_heartbeat(ELAPSED_ROUNDING_INPUT, TURN_COUNT) == (
        f"[HEARTBEAT t={ELAPSED_ROUNDING_OUTPUT}s turn={TURN_COUNT}] continue, "
        "the match is still live."
    )


def test_format_heartbeat_zero_values() -> None:
    assert format_heartbeat(ZERO_ELAPSED, ZERO_TURN) == (
        "[HEARTBEAT t=0s turn=0] continue, the match is still live."
    )


def test_inject_appends_user_heartbeat_when_last_role_is_assistant() -> None:
    chat = _chat(["assistant"])
    before = len(chat.history)

    injected = inject(chat, ELAPSED_ROUNDING_INPUT, TURN_COUNT)

    assert injected == format_heartbeat(ELAPSED_ROUNDING_INPUT, TURN_COUNT)
    assert len(chat.history) == before + 1
    assert chat.history[-1].role == "user"
    assert chat.history[-1].content.startswith("[HEARTBEAT t=")


def test_inject_raises_when_last_role_is_user_and_keeps_history() -> None:
    chat = _chat(["assistant", "user"])
    before = chat.history

    with pytest.raises(LifecycleError) as exc_info:
        inject(chat, ELAPSED_ROUNDING_INPUT, TURN_COUNT)

    assert chat.history == before
    assert exc_info.value.state == "user"
    assert exc_info.value.event == "heartbeat_inject"


def test_inject_returns_exact_injected_string() -> None:
    chat = _chat(["assistant"])

    injected = inject(chat, ZERO_ELAPSED, ZERO_TURN)

    assert injected == format_heartbeat(ZERO_ELAPSED, ZERO_TURN)
