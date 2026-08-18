from __future__ import annotations

from typing import Protocol


class Message(Protocol):
    content: str | None


class Choice(Protocol):
    message: Message
    finish_reason: str | None


class Usage(Protocol):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CompletionResponse(Protocol):
    choices: list[Choice]
    usage: Usage
