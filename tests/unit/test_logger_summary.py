"""Unit tests for MatchLogger.write_summary — split from test_logger.py for LOC cap."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from common.ids import make_match_id
from orchestrator.logger import MatchLogger

LOG_ROOT_NAME = "logs"
MATCH_ID_VALUE = "match-001"
TWO_AGENTS = 2
SUMMARY_RESULT = "victory"
SUMMARY_WINNER = 0
SUMMARY_CAUSE = "opponent_crashed"
SUMMARY_FINAL_STATE = "DONE"


def _match_logger(tmp_path: Path, n_agents: int) -> MatchLogger:
    return MatchLogger(tmp_path / LOG_ROOT_NAME, make_match_id(MATCH_ID_VALUE), n_agents)


def test_write_summary_overwrites_sorted_json(tmp_path: Path) -> None:
    logger = _match_logger(tmp_path, TWO_AGENTS)

    logger.write_summary(
        {
            "result": SUMMARY_RESULT,
            "winner": SUMMARY_WINNER,
            "cause": SUMMARY_CAUSE,
            "final_state": SUMMARY_FINAL_STATE,
        }
    )

    summary_text = (logger.match_dir / "summary.json").read_text(encoding="utf-8")
    assert summary_text == (
        "{\n"
        f'  "cause": "{SUMMARY_CAUSE}",\n'
        f'  "final_state": "{SUMMARY_FINAL_STATE}",\n'
        f'  "result": "{SUMMARY_RESULT}",\n'
        f'  "winner": {SUMMARY_WINNER}\n'
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


def test_write_summary_rejects_winner_above_max(tmp_path: Path) -> None:
    """Round-6 lock: winner above 15 (plan §3.A7 N ≤ 16) must be rejected."""
    logger = _match_logger(tmp_path, TWO_AGENTS)

    with pytest.raises(ValidationError):
        logger.write_summary(
            {"result": "victory", "winner": 16, "cause": "x", "final_state": "DONE"}
        )


def test_write_summary_rejects_unknown_result(tmp_path: Path) -> None:
    logger = _match_logger(tmp_path, TWO_AGENTS)

    with pytest.raises(ValidationError):
        logger.write_summary({"result": "bogus", "winner": 0, "cause": "x", "final_state": "DONE"})
