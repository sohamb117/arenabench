import json
from pathlib import Path
from typing import Annotated, Literal, cast

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from orchestrator.lifecycle import MatchOutcome

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = REPO_ROOT / "orchestrator" / "schemas" / "summary.schema.json"
MAX_AGENT_SLOT = 15  # plan §3: N ≤ 16 agents, slot ∈ [0, 15]


class _SummaryShape(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: Literal["victory", "draw", "timeout", "error"]
    winner: Annotated[int, Field(ge=0, le=15)] | None
    cause: Annotated[str, Field(min_length=1)]
    final_state: Literal["DONE"]
    alive_at_timeout: list[Annotated[int, Field(ge=0, le=15)]] | None = None
    total_duration_s: Annotated[float, Field(ge=0.0)] | None = None


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


def test_summary_shape_rejects_winner_above_max() -> None:
    """winner is capped at 15 (matches summary.schema.json + plan §3 max 16 agents)."""
    with pytest.raises(ValidationError):
        _SummaryShape.model_validate(
            {
                "result": "victory",
                "winner": MAX_AGENT_SLOT + 1,
                "cause": "x",
                "final_state": "DONE",
            }
        )


def test_summary_shape_rejects_winner_below_zero() -> None:
    with pytest.raises(ValidationError):
        _SummaryShape.model_validate(
            {"result": "victory", "winner": -1, "cause": "x", "final_state": "DONE"}
        )


def test_summary_shape_rejects_empty_cause() -> None:
    """cause must have minLength 1 per summary.schema.json (always meaningful)."""
    with pytest.raises(ValidationError):
        _SummaryShape.model_validate(
            {"result": "victory", "winner": 0, "cause": "", "final_state": "DONE"}
        )


def test_schema_file_constraints_match_pydantic_mirror() -> None:
    """Drift guard: schema file's winner.maximum + cause.minLength must mirror this test's
    _SummaryShape constraints exactly.
    """
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    schema = cast(dict[str, object], json.loads(raw))
    props = cast(dict[str, object], schema["properties"])
    winner = cast(dict[str, object], props["winner"])
    any_of = cast(list[dict[str, object]], winner["anyOf"])
    int_branch = next(b for b in any_of if b.get("type") == "integer")
    assert int_branch["minimum"] == 0
    assert int_branch["maximum"] == MAX_AGENT_SLOT
    cause = cast(dict[str, object], props["cause"])
    assert cause["minLength"] == 1
