from __future__ import annotations

import hashlib
import json

import pytest

from common.clock import now_utc
from common.context_protocol import ContextChunkError, context_chunks, decode_context_chunks
from common.protocol import MAX_FRAME_BYTES, Envelope, serialize_envelope
from harness.attempt_logging import AttemptIdentity


def test_chunks_reconstruct_byte_exact_unicode_canonical_payload() -> None:
    messages = [
        {"role": "system", "content": "秘密 café 🔐\n" + "界" * 30_000},
        {"role": "user", "content": 'line\r\nquote: " exact'},
    ]

    chunks = context_chunks(AttemptIdentity(4, "req-unicode", 1), messages)
    decoded = decode_context_chunks(chunks)

    canonical = json.dumps(messages, separators=(",", ":"), ensure_ascii=False).encode()
    assert decoded == messages
    assert chunks[0].total_bytes == len(canonical)
    assert chunks[0].sha256 == hashlib.sha256(canonical).hexdigest()
    for seq, chunk in enumerate(chunks):
        envelope = Envelope(ts=now_utc(), seq=seq, src="agent0", kind=chunk.kind, data=chunk)
        assert len(serialize_envelope(envelope).encode()) <= MAX_FRAME_BYTES


@pytest.mark.parametrize("mutation", ["missing", "order", "hash"])
def test_decode_rejects_missing_corrupt_or_hash_mismatched_chunks(mutation: str) -> None:
    chunks = list(
        context_chunks(
            AttemptIdentity(0, "req", 0),
            [{"role": "user", "content": "界" * MAX_FRAME_BYTES}],
        )
    )
    if mutation == "missing":
        chunks.pop()
    elif mutation == "order":
        chunks[0], chunks[1] = chunks[1], chunks[0]
    else:
        chunks = [chunk.model_copy(update={"sha256": "0" * 64}) for chunk in chunks]

    expected = {
        "missing": "missing_chunks",
        "order": "out_of_order_chunks",
        "hash": "hash_mismatch",
    }
    with pytest.raises(ContextChunkError, match=expected[mutation]):
        decode_context_chunks(chunks)
