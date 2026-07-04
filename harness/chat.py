# pyright: reportUnknownMemberType=false
# Chat history shape adapted from terminal-bench Terminus 2
# (https://github.com/laude-institute/terminal-bench @ 1a6ffa9, Apache-2.0).
# Reimplemented for arenabench; no upstream code copied verbatim.

from typing import Literal

import litellm
from pydantic import BaseModel, ConfigDict

from common.errors import LifecycleError

_MIN_LEN_FOR_TRIM = 3


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    role: Literal["user", "assistant"]
    content: str


class Chat:
    """
    Alternating-role message history with token accounting.

    Invariants:
    - First message is always user (the rendered initial prompt template).
    - Subsequent messages strictly alternate user/assistant.
    - max_context_tokens > summarize_below_free_tokens > 0.
    - history is never empty after construction.
    """

    def __init__(
        self,
        initial_user_prompt: str,
        model: str,
        max_context_tokens: int,
        summarize_below_free_tokens: int = 8_000,
        terminal_output_truncate_bytes: int = 10_240,
    ) -> None:
        self._model = model
        self._max_context_tokens = max_context_tokens
        self._summarize_below_free_tokens = summarize_below_free_tokens
        self._terminal_output_truncate_bytes = terminal_output_truncate_bytes
        self._messages: list[Message] = [Message(role="user", content=initial_user_prompt)]

    @property
    def history(self) -> tuple[Message, ...]:
        return tuple(self._messages)

    @property
    def prompt_tokens(self) -> int:
        messages_dict: list[dict[str, str]] = [
            {"role": msg.role, "content": msg.content} for msg in self._messages
        ]
        return litellm.token_counter(model=self._model, messages=messages_dict)

    @property
    def free_tokens(self) -> int:
        return self._max_context_tokens - self.prompt_tokens

    def append_user(self, content: str) -> None:
        last_role = self._messages[-1].role
        if last_role == "user":
            raise LifecycleError(
                "Cannot append user message after user message",
                state=last_role,
                event="append_user",
            )
        self._messages.append(Message(role="user", content=content))

    def append_assistant(self, content: str) -> None:
        last_role = self._messages[-1].role
        if last_role == "assistant":
            raise LifecycleError(
                "Cannot append assistant message after assistant message",
                state=last_role,
                event="append_assistant",
            )
        self._messages.append(Message(role="assistant", content=content))

    def merge_into_last_user(self, extra: str) -> None:
        """Append `extra` to the last user message's content in place.

        Used by heartbeat injection when a tick arrives while the chat is
        already user-last (bash_result appended, next LLM call not yet made).
        Preserves the alternating-role invariant that a plain append_user
        would violate.
        """
        if not self._messages or self._messages[-1].role != "user":
            raise LifecycleError(
                "Cannot merge into last user message: last role is not user",
                state=self._messages[-1].role if self._messages else "empty",
                event="merge_into_last_user",
            )
        last = self._messages[-1]
        self._messages[-1] = Message(role="user", content=last.content + extra)

    def truncate_terminal_output(self, raw: str) -> str:
        """Returns head + truncation marker + tail when raw > truncate bytes."""
        raw_bytes = raw.encode()
        if len(raw_bytes) <= self._terminal_output_truncate_bytes:
            return raw

        half = self._terminal_output_truncate_bytes // 2
        head = raw_bytes[:half]
        tail = raw_bytes[-half:]
        dropped = len(raw_bytes) - 2 * half
        marker = f"\n...[truncated {dropped} bytes]...\n".encode()
        return (head + marker + tail).decode("utf-8", errors="replace")

    def trim_to_fit(self) -> int:
        """
        Drop the oldest user/assistant PAIR (never the very first user turn)
        until prompt_tokens <= max_context_tokens. Return number of pairs dropped.
        """
        dropped_pairs = 0
        while (
            self.prompt_tokens > self._max_context_tokens
            and len(self._messages) >= _MIN_LEN_FOR_TRIM
        ):
            del self._messages[1:3]
            dropped_pairs += 1
        return dropped_pairs

    def should_summarize(self) -> bool:
        return self.free_tokens < self._summarize_below_free_tokens

    def summarize(self, summary: str) -> None:
        """Replace all but the first user turn with a single assistant 'context summary' turn."""
        self._messages = [self._messages[0], Message(role="assistant", content=summary)]
