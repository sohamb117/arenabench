import json
from pathlib import Path
from typing import Literal, cast

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from orchestrator.lifecycle import MatchOutcome

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "orchestrator" / "schemas" / "summary.schema.json"


class _SummaryShape(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: Literal["victory", "draw", "timeout", "error"]
    winner: int | None
    cause: str
    final_state: Literal["DONE"]


def test_schema_file_exists_and_is_valid_json() -> None:
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    parsed = cast(object, json.loads(raw))
    assert isinstance(parsed, dict)


def test_match_outcome_pydantic_model_round_trips() -> None:
    out = MatchOutcome(result="victory", winner=0, cause="opponent_crashed", final_state="DONE")
    dumped = out.model_dump()
    rebuilt = _SummaryShape.model_validate(dumped)
    assert rebuilt.result == "victory"
    assert rebuilt.winner == 0
    assert rebuilt.cause == "opponent_crashed"


def test_summary_shape_rejects_unknown_result() -> None:
    with pytest.raises(ValidationError):
        _SummaryShape.model_validate(
            {"result": "bogus", "winner": None, "cause": "x", "final_state": "DONE"}
        )


def test_summary_shape_rejects_non_done_final_state() -> None:
    with pytest.raises(ValidationError):
        _SummaryShape.model_validate(
            {"result": "victory", "winner": 0, "cause": "x", "final_state": "ARCHIVING"}
        )


def test_summary_shape_accepts_null_winner_for_draw_and_timeout() -> None:
    _SummaryShape.model_validate(
        {"result": "draw", "winner": None, "cause": "mutual_destruction", "final_state": "DONE"}
    )
    _SummaryShape.model_validate(
        {
            "result": "timeout",
            "winner": None,
            "cause": "max_duration_exceeded",
            "final_state": "DONE",
        }
    )


def test_summary_shape_rejects_extra_keys() -> None:
    with pytest.raises(ValidationError):
        _SummaryShape.model_validate(
            {
                "result": "victory",
                "winner": 0,
                "cause": "x",
                "final_state": "DONE",
                "extra_field": "rejected",
            }
        )
