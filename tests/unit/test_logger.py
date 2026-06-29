from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from common.errors import LifecycleError
from common.ids import make_match_id
from common.protocol import (
    BashCommand,
    BashRequest,
    BashResult,
    Envelope,
    Frame,
    HarnessExit,
    HeartbeatInjected,
    LlmRequest,
    LlmResponse,
    PidAnnounce,
    TurnSummary,
    serialize_envelope,
)
from orchestrator.logger import MatchLogger

NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
LOG_ROOT_NAME = "logs"
MATCH_ID_VALUE = "match-001"
WROTE_SUMMARY_RESULT = "victory"
WROTE_SUMMARY_WINNER = 0
WROTE_SUMMARY_CAUSE = "opponent_crashed"
WROTE_SUMMARY_FINAL_STATE = "DONE"
ZERO_SLOT = 0
ONE_SLOT = 1
TWO_AGENTS = 2
FOUR_AGENTS = 4
REQUEST_ID_1 = "req-1"
REQUEST_ID_2 = "req-2"


def _env(src: str, data: Frame, seq: int) -> Envelope:
    return Envelope(v=1, ts=NOW, seq=seq, src=src, kind=data.kind, data=data)


def _match_logger(tmp_path: Path, n_agents: int) -> MatchLogger:
    return MatchLogger(tmp_path / LOG_ROOT_NAME, make_match_id(MATCH_ID_VALUE), n_agents)


def test_constructor_creates_match_tree_for_two_agents(tmp_path: Path) -> None:
    logger = _match_logger(tmp_path, TWO_AGENTS)

    match_dir = tmp_path / LOG_ROOT_NAME / "matches" / MATCH_ID_VALUE
    assert logger.match_dir == match_dir
    assert (match_dir / "match.jsonl").read_text(encoding="utf-8") == ""
    assert (match_dir / "orchestrator.log").read_text(encoding="utf-8") == ""
    assert not (match_dir / "summary.json").exists()

    for slot in (ZERO_SLOT, ONE_SLOT):
        slot_dir = match_dir / "agents" / f"{slot:02d}"
        assert slot_dir.is_dir()
        for name in ("events.jsonl", "bash.jsonl", "api.jsonl", "context.jsonl"):
            assert (slot_dir / name).read_text(encoding="utf-8") == ""


def test_constructor_creates_four_agent_slots(tmp_path: Path) -> None:
    logger = _match_logger(tmp_path, FOUR_AGENTS)

    match_dir = logger.match_dir
    for slot in range(FOUR_AGENTS):
        slot_dir = match_dir / "agents" / f"{slot:02d}"
        assert slot_dir.is_dir()
        assert (slot_dir / "bash.jsonl").exists()
        assert (slot_dir / "api.jsonl").exists()
        assert (slot_dir / "events.jsonl").exists()
        assert (slot_dir / "context.jsonl").exists()


def test_write_envelope_routes_one_frame_per_file(tmp_path: Path) -> None:
    logger = _match_logger(tmp_path, TWO_AGENTS)

    envelopes = [
        _env(
            "agent0",
            PidAnnounce(pid=11, user="u", uid=1000, hostname="host", parser="json", model="m"),
            0,
        ),
        _env(
            "agent0",
            BashRequest(
                turn=1,
                request_id=REQUEST_ID_1,
                commands=[BashCommand(keystrokes="ls\n", duration_sec=0.2, is_blocking=True)],
            ),
            1,
        ),
        _env(
            "agent0",
            LlmRequest(
                turn=2,
                request_id=REQUEST_ID_2,
                model="m",
                messages_count=3,
                prompt_chars=120,
                temperature=0.1,
            ),
            2,
        ),
        _env(
            "agent0",
            TurnSummary(turn=3, action_count=4, free_tokens=5, summarized=True, history_chars=6),
            3,
        ),
        _env(
            "orchestrator",
            HarnessExit(reason="clean", code=0, last_turn=7),
            4,
        ),
        _env(
            "guest-probe",
            HeartbeatInjected(turn=5, elapsed_s=1.5, payload="ping"),
            5,
        ),
        _env(
            "agent1",
            LlmResponse(
                turn=2,
                request_id=REQUEST_ID_2,
                content="ok",
                parser="json",
                parse_ok=True,
                parsed={"ok": True},
                prompt_tokens=1,
                completion_tokens=2,
                total_tokens=3,
                cost_usd=0.5,
                latency_s=0.25,
                error=None,
            ),
            6,
        ),
    ]

    for env in envelopes:
        logger.write_envelope(env)

    match_dir = logger.match_dir
    assert (match_dir / "agents" / "00" / "bash.jsonl").read_text(encoding="utf-8") == (
        f"{serialize_envelope(envelopes[0])}\n{serialize_envelope(envelopes[1])}\n"
    )
    assert (match_dir / "agents" / "00" / "api.jsonl").read_text(encoding="utf-8") == (
        f"{serialize_envelope(envelopes[2])}\n"
    )
    assert (match_dir / "agents" / "00" / "events.jsonl").read_text(encoding="utf-8") == (
        f"{serialize_envelope(envelopes[3])}\n"
    )
    assert (match_dir / "match.jsonl").read_text(encoding="utf-8") == (
        f"{serialize_envelope(envelopes[4])}\n{serialize_envelope(envelopes[5])}\n"
    )
    assert (match_dir / "agents" / "01" / "api.jsonl").read_text(encoding="utf-8") == (
        f"{serialize_envelope(envelopes[6])}\n"
    )


def test_write_envelope_appends_in_order_to_same_file(tmp_path: Path) -> None:
    logger = _match_logger(tmp_path, TWO_AGENTS)

    first = _env(
        "agent0",
        BashResult(
            turn=1,
            request_id=REQUEST_ID_1,
            terminal_output="one",
            truncated_bytes=0,
            exit_status=0,
            duration_s=0.1,
        ),
        0,
    )
    second = _env(
        "agent0",
        BashResult(
            turn=1,
            request_id=REQUEST_ID_2,
            terminal_output="two",
            truncated_bytes=0,
            exit_status=1,
            duration_s=0.2,
        ),
        1,
    )

    logger.write_envelope(first)
    logger.write_envelope(second)

    expected = f"{serialize_envelope(first)}\n{serialize_envelope(second)}\n"
    actual = (logger.match_dir / "agents" / "00" / "bash.jsonl").read_text(encoding="utf-8")
    assert actual == expected


def test_unknown_src_raises_lifecycle_error(tmp_path: Path) -> None:
    logger = _match_logger(tmp_path, TWO_AGENTS)
    envelope = _env(
        "unknown",
        TurnSummary(turn=1, action_count=1, free_tokens=1, summarized=False, history_chars=1),
        0,
    )

    with pytest.raises(LifecycleError):
        logger.write_envelope(envelope)


def test_write_summary_overwrites_sorted_json(tmp_path: Path) -> None:
    logger = _match_logger(tmp_path, TWO_AGENTS)

    logger.write_summary(
        {
            "result": WROTE_SUMMARY_RESULT,
            "winner": WROTE_SUMMARY_WINNER,
            "cause": WROTE_SUMMARY_CAUSE,
            "final_state": WROTE_SUMMARY_FINAL_STATE,
        }
    )

    summary_text = (logger.match_dir / "summary.json").read_text(encoding="utf-8")
    assert summary_text == (
        "{\n"
        f'  "cause": "{WROTE_SUMMARY_CAUSE}",\n'
        f'  "final_state": "{WROTE_SUMMARY_FINAL_STATE}",\n'
        f'  "result": "{WROTE_SUMMARY_RESULT}",\n'
        f'  "winner": {WROTE_SUMMARY_WINNER}\n'
        "}"
    )


def test_write_summary_rejects_invalid_dict(tmp_path: Path) -> None:
    """Drift guard: write_summary must reject inputs that violate summary.schema.json."""
    logger = _match_logger(tmp_path, TWO_AGENTS)

    with pytest.raises(ValidationError):
        logger.write_summary(
            {"result": "victory", "winner": "agent0", "cause": "x", "final_state": "DONE"}
        )
    with pytest.raises(ValidationError):
        logger.write_summary({"result": "victory", "winner": 0, "cause": "", "final_state": "DONE"})


def test_context_manager_closes_files(tmp_path: Path) -> None:
    envelope = _env(
        "agent0",
        BashRequest(
            turn=1,
            request_id=REQUEST_ID_1,
            commands=[BashCommand(keystrokes="pwd\n", duration_sec=0.2, is_blocking=True)],
        ),
        0,
    )

    with MatchLogger(tmp_path / LOG_ROOT_NAME, make_match_id(MATCH_ID_VALUE), TWO_AGENTS) as logger:
        logger.write_envelope(envelope)

    with pytest.raises(ValueError, match="closed file"):
        logger.write_envelope(envelope)
