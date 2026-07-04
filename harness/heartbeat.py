from __future__ import annotations

from harness.chat import Chat

HEARTBEAT_PREFIX = "[HEARTBEAT t={elapsed_s}s turn={turn_count}] continue, the match is still live."
_MERGE_SEPARATOR = "\n\n"


def format_heartbeat(elapsed_s: float, turn_count: int) -> str:
    return HEARTBEAT_PREFIX.format(elapsed_s=round(elapsed_s), turn_count=turn_count)


def inject(chat: Chat, elapsed_s: float, turn_count: int) -> str:
    heartbeat = format_heartbeat(elapsed_s, turn_count)
    history = chat.history
    if history and history[-1].role == "user":
        chat.merge_into_last_user(f"{_MERGE_SEPARATOR}{heartbeat}")
    else:
        chat.append_user(heartbeat)
    return heartbeat
