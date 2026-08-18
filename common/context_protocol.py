from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True)
_RAW_CHUNK_BYTES = 36 * 1024
_MAX_ENCODED_CHUNK_CHARS = 48 * 1024
_MESSAGES_ADAPTER = TypeAdapter(tuple["LlmMessage", ...])


class ContextIdentity(Protocol):
    @property
    def turn(self) -> int: ...

    @property
    def request_id(self) -> str: ...

    @property
    def attempt(self) -> int: ...


class LlmMessagePayload(TypedDict):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class LlmMessage(BaseModel):
    model_config = _MODEL_CONFIG

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class LlmContextSnapshot(BaseModel):
    model_config = _MODEL_CONFIG
    kind: Literal["llm_context_snapshot"] = "llm_context_snapshot"
    turn: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128)
    attempt: int = Field(ge=0, le=10)
    messages: list[LlmMessage] = Field(min_length=1)


class LlmContextChunk(BaseModel):
    model_config = _MODEL_CONFIG

    kind: Literal["llm_context_chunk"] = "llm_context_chunk"
    turn: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=128)
    attempt: int = Field(ge=0, le=10)
    chunk_index: int = Field(ge=0)
    chunk_count: int = Field(ge=1)
    total_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_b64: str = Field(min_length=1, max_length=_MAX_ENCODED_CHUNK_CHARS)


type ContextChunkReason = Literal[
    "missing_chunks",
    "out_of_order_chunks",
    "metadata_mismatch",
    "invalid_base64",
    "size_mismatch",
    "hash_mismatch",
    "invalid_payload",
]


@dataclass(slots=True)
class ContextChunkError(Exception):
    reason: ContextChunkReason

    def __str__(self) -> str:
        return self.reason


def canonical_context_bytes(messages: Sequence[Mapping[str, str]]) -> bytes:
    validated = tuple(LlmMessage.model_validate(message) for message in messages)
    body = [message.model_dump(mode="json") for message in validated]
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()


def context_chunks(
    identity: ContextIdentity, messages: Sequence[Mapping[str, str]]
) -> tuple[LlmContextChunk, ...]:
    payload = canonical_context_bytes(messages)
    digest = hashlib.sha256(payload).hexdigest()
    pieces = tuple(
        payload[offset : offset + _RAW_CHUNK_BYTES]
        for offset in range(0, len(payload), _RAW_CHUNK_BYTES)
    )
    return tuple(
        LlmContextChunk(
            turn=identity.turn,
            request_id=identity.request_id,
            attempt=identity.attempt,
            chunk_index=index,
            chunk_count=len(pieces),
            total_bytes=len(payload),
            sha256=digest,
            payload_b64=base64.b64encode(piece).decode("ascii"),
        )
        for index, piece in enumerate(pieces)
    )


def context_payload(chunks: Sequence[LlmContextChunk]) -> bytes:
    if not chunks:
        raise ContextChunkError("missing_chunks")
    first = chunks[0]
    if len(chunks) != first.chunk_count:
        raise ContextChunkError("missing_chunks")
    if [chunk.chunk_index for chunk in chunks] != list(range(first.chunk_count)):
        raise ContextChunkError("out_of_order_chunks")
    identity = (first.turn, first.request_id, first.attempt)
    metadata = (first.chunk_count, first.total_bytes, first.sha256)
    if any(
        (chunk.turn, chunk.request_id, chunk.attempt) != identity
        or (chunk.chunk_count, chunk.total_bytes, chunk.sha256) != metadata
        for chunk in chunks
    ):
        raise ContextChunkError("metadata_mismatch")
    try:
        payload = b"".join(base64.b64decode(chunk.payload_b64, validate=True) for chunk in chunks)
    except (binascii.Error, ValueError) as exc:
        raise ContextChunkError("invalid_base64") from exc
    if len(payload) != first.total_bytes:
        raise ContextChunkError("size_mismatch")
    if hashlib.sha256(payload).hexdigest() != first.sha256:
        raise ContextChunkError("hash_mismatch")
    return payload


def decode_context_chunks(chunks: Sequence[LlmContextChunk]) -> list[LlmMessagePayload]:
    try:
        messages = _MESSAGES_ADAPTER.validate_json(context_payload(chunks))
    except ValidationError as exc:
        raise ContextChunkError("invalid_payload") from exc
    return [LlmMessagePayload(role=message.role, content=message.content) for message in messages]
