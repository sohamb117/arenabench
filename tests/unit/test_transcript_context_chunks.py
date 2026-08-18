from __future__ import annotations

from pathlib import Path

import pytest

from common.context_protocol import context_chunks
from harness.attempt_logging import AttemptIdentity
from orchestrator.transcript import read_transcript
from tests.unit.transcript_fixtures import JsonRecord, envelope, exact_run, write_jsonl


def _chunk_records(mutation: str | None = None) -> list[JsonRecord]:
    messages = [
        {"role": "system", "content": "秘密\n" + "界" * 30_000},
        {"role": "user", "content": "继续 café 🔐"},
    ]
    chunks = list(context_chunks(AttemptIdentity(2, "req-unicode", 0), messages))
    if mutation == "missing":
        chunks.pop()
    elif mutation == "order":
        chunks.reverse()
    elif mutation == "payload":
        chunks[0] = chunks[0].model_copy(update={"payload_b64": "%%%"})
    elif mutation == "hash":
        chunks = [chunk.model_copy(update={"sha256": "0" * 64}) for chunk in chunks]
    return [
        envelope(
            "2026-08-17T12:00:01Z",
            "agent0",
            "llm_context_chunk",
            chunk.model_dump(mode="json", exclude={"kind"}),
        )
        for chunk in chunks
    ]


def test_reader_reconstructs_exact_chunked_context(tmp_path: Path) -> None:
    run = exact_run(tmp_path)
    records = _chunk_records()
    write_jsonl(run / "agents" / "00" / "context.jsonl", records)

    context = read_transcript(run).agents[0].turns[0].attempts[0].context

    assert context.kind == "context"
    assert [message.content for message in context.messages] == [
        "秘密\n" + "界" * 30_000,
        "继续 café 🔐",
    ]


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", "missing_chunks"),
        ("order", "out_of_order_chunks"),
        ("payload", "invalid_base64"),
        ("hash", "hash_mismatch"),
    ],
)
def test_reader_marks_invalid_chunk_sets_unavailable(
    tmp_path: Path, mutation: str, reason: str
) -> None:
    run = exact_run(tmp_path)
    records = _chunk_records(mutation)
    write_jsonl(run / "agents" / "00" / "context.jsonl", records)

    context = read_transcript(run).agents[0].turns[0].attempts[0].context

    assert context.kind == "unavailable"
    assert context.reason == reason
