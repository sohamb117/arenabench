from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from common.context_protocol import ContextChunkError, LlmContextChunk, decode_context_chunks
from orchestrator.transcript import Context, Message, UnavailableContext
from orchestrator.transcript_frames import ContextFrame


def assemble_context(
    snapshot: ContextFrame | None,
    chunks: Sequence[LlmContextChunk],
    context_failure: Literal["context_logging_error", "context_too_large"] | None,
    *,
    legacy: bool,
) -> Context | UnavailableContext:
    if context_failure is not None:
        return UnavailableContext(reason="context_logging_error")
    if chunks:
        try:
            messages = decode_context_chunks(chunks)
        except ContextChunkError as exc:
            match exc.reason:
                case "hash_mismatch":
                    reason = "hash_mismatch"
                case "missing_chunks" | "out_of_order_chunks":
                    reason = "missing_chunks"
                case "metadata_mismatch" | "invalid_base64" | "size_mismatch" | "invalid_payload":
                    reason = "corrupt_chunks"
            return UnavailableContext(reason=reason)
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
