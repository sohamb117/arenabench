from __future__ import annotations

from common.errors import LifecycleError
from harness.chat import Chat

HEARTBEAT_PREFIX = "[HEARTBEAT t={elapsed_s}s turn={turn_count}] continue, the match is still live."


def format_heartbeat(elapsed_s: float, turn_count: int) -> str:
    return HEARTBEAT_PREFIX.format(elapsed_s=round(elapsed_s), turn_count=turn_count)


def inject(chat: Chat, elapsed_s: float, turn_count: int) -> str:
    history = chat.history
    if history and history[-1].role == "user":
        raise LifecycleError(
            "heartbeat injection blocked",
            state=history[-1].role,
            event="heartbeat_inject",
        )

    heartbeat = format_heartbeat(elapsed_s, turn_count)
    chat.append_user(heartbeat)
    return heartbeat
