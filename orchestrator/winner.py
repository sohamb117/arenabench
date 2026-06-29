from typing import Literal

from pydantic import BaseModel, ConfigDict

from common.ids import AgentSlot


class WinnerOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    result: Literal["in_progress", "victory", "draw", "timeout", "error"]
    winner: AgentSlot | None
    cause: str
    alive_at_decision: tuple[AgentSlot, ...]


def resolve(  # noqa: PLR0911
    *,
    alive: set[AgentSlot],
    elapsed_s: float,
    grace_started_s: float | None,
    grace_period_s: float,
    max_duration_s: float,
    last_alive_count: int,
) -> WinnerOutcome:
    alive_count = len(alive)
    alive_tuple = tuple(sorted(alive))

    if alive_count == 0:
        if grace_started_s is not None:
            return WinnerOutcome(
                result="draw",
                winner=None,
                cause="survivor_died_in_grace",
                alive_at_decision=alive_tuple,
            )
        return WinnerOutcome(
            result="draw",
            winner=None,
            cause="mutual_destruction",
            alive_at_decision=alive_tuple,
        )

    if alive_count == 1:
        winner = alive_tuple[0]
        if grace_started_s is None:
            return WinnerOutcome(
                result="in_progress",
                winner=None,
                cause="grace_started",
                alive_at_decision=alive_tuple,
            )
        time_in_grace = elapsed_s - grace_started_s
        if time_in_grace < grace_period_s:
            return WinnerOutcome(
                result="in_progress",
                winner=None,
                cause="grace_in_progress",
                alive_at_decision=alive_tuple,
            )
        cause = "opponent_crashed" if last_alive_count > 1 else "solo_survivor"
        return WinnerOutcome(
            result="victory",
            winner=winner,
            cause=cause,
            alive_at_decision=alive_tuple,
        )

    if grace_started_s is not None:
        return WinnerOutcome(
            result="error",
            winner=None,
            cause="inconsistent_state_grace_with_multi_alive",
            alive_at_decision=alive_tuple,
        )
    if elapsed_s > max_duration_s:
        return WinnerOutcome(
            result="timeout",
            winner=None,
            cause="max_duration_exceeded",
            alive_at_decision=alive_tuple,
        )
    return WinnerOutcome(
        result="in_progress",
        winner=None,
        cause="match_continues",
        alive_at_decision=alive_tuple,
    )
