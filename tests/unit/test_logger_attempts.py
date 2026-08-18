from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from common.ids import make_match_id
from common.protocol import (
    Envelope,
    Frame,
    LlmAttemptFailure,
    LlmContextSnapshot,
    LlmMessage,
    serialize_envelope,
)
from orchestrator.logger import MatchLogger

NOW = datetime(2026, 1, 1, tzinfo=UTC)
MATCH_ID = make_match_id("match-001")


def _env(data: Frame, seq: int) -> Envelope:
    return Envelope(ts=NOW, seq=seq, src="agent0", kind=data.kind, data=data)


def test_constructor_restricts_created_log_modes_to_owner(tmp_path: Path) -> None:
    logger = MatchLogger(tmp_path / "logs", MATCH_ID, 1)
    logger.close()

    match_dir = logger.match_dir
    paths = [
        tmp_path / "logs",
        tmp_path / "logs" / "matches",
        match_dir,
        match_dir / "agents",
        match_dir / "agents" / "00",
        match_dir / "match.jsonl",
        match_dir / "orchestrator.log",
        match_dir / "agents" / "00" / "context.jsonl",
    ]

    assert [(path.stat().st_mode & 0o777) for path in paths] == [
        0o700,
        0o700,
        0o700,
        0o700,
        0o700,
        0o600,
        0o600,
        0o600,
    ]


def test_write_envelope_routes_attempt_context_and_failure_away_from_api(tmp_path: Path) -> None:
    logger = MatchLogger(tmp_path / "logs", MATCH_ID, 1)
    snapshot = _env(
        LlmContextSnapshot(
            turn=2,
            request_id="req-2",
            attempt=0,
            messages=[LlmMessage(role="user", content="exact 🔐 context")],
        ),
        0,
    )
    failure = _env(
        LlmAttemptFailure(
            turn=2,
            request_id="req-2",
            attempt=0,
            category="provider_refusal",
            finish_reason="content_filter",
            error_text="provider returned no text",
            prompt_tokens=211,
            completion_tokens=0,
            total_tokens=211,
            latency_s=0.25,
        ),
        1,
    )

    logger.write_envelope(snapshot)
    logger.write_envelope(failure)
    logger.close()

    slot_dir = logger.match_dir / "agents" / "00"
    assert (slot_dir / "api.jsonl").read_text(encoding="utf-8") == ""
    assert (slot_dir / "context.jsonl").read_text(encoding="utf-8") == (
        f"{serialize_envelope(snapshot)}\n{serialize_envelope(failure)}\n"
    )
