from __future__ import annotations

from collections.abc import Sequence

from common.context_protocol import ContextChunkError, LlmContextChunk, decode_context_chunks
from orchestrator.transcript import Context, Message, UnavailableContext
from orchestrator.transcript_frames import ContextFrame


def assemble_context(
    snapshot: ContextFrame | None,
    chunks: Sequence[LlmContextChunk],
    *,
    legacy: bool,
) -> Context | UnavailableContext:
    if chunks:
        try:
            messages = decode_context_chunks(chunks)
        except ContextChunkError as exc:
            return UnavailableContext(reason=exc.reason)
        return Context(
            messages=tuple(
                Message(role=message["role"], content=message["content"]) for message in messages
            )
        )
    if snapshot is not None:
        return Context(
            messages=tuple(
                Message(role=message.role, content=message.content) for message in snapshot.messages
            )
        )
    return UnavailableContext(reason="legacy_log" if legacy else "not_recorded")
