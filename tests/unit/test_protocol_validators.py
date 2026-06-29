import json
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from common.protocol import (
    MAX_FRAME_BYTES,
    BashResult,
    Envelope,
    HarnessExit,
    HeartbeatTick,
    Shutdown,
    parse_envelope,
    serialize_envelope,
)


def test_negative_seq_rejected() -> None:
    with pytest.raises(ValidationError):
        Envelope(
            v=1,
            ts=datetime(2026, 6, 28, 19, 0, 0, tzinfo=UTC),
            seq=-1,
            src="orchestrator",
            dst="agent0",
            kind="shutdown",
            data=Shutdown(kind="shutdown", reason="stop"),
        )


def test_ts_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        Envelope(
            v=1,
            ts=datetime(2026, 6, 28, 19, 0, 0),
            seq=0,
            src="agent0",
            kind="harness_exit",
            data=HarnessExit(kind="harness_exit", reason="clean", code=0, last_turn=1),
        )


def test_ts_rejects_non_utc_offset() -> None:
    with pytest.raises(ValidationError):
        Envelope(
            v=1,
            ts=datetime(2026, 6, 28, 19, 0, 0, tzinfo=timezone(timedelta(hours=5))),
            seq=0,
            src="agent0",
            kind="harness_exit",
            data=HarnessExit(kind="harness_exit", reason="clean", code=0, last_turn=1),
        )


def test_dst_required_for_heartbeat_tick() -> None:
    with pytest.raises(ValidationError):
        Envelope(
            v=1,
            ts=datetime(2026, 6, 28, 19, 0, 0, tzinfo=UTC),
            seq=0,
            src="orchestrator",
            kind="heartbeat_tick",
            data=HeartbeatTick(kind="heartbeat_tick", elapsed_s=1.0, turn_hint=1),
        )


def test_dst_required_for_shutdown() -> None:
    with pytest.raises(ValidationError):
        Envelope(
            v=1,
            ts=datetime(2026, 6, 28, 19, 0, 0, tzinfo=UTC),
            seq=0,
            src="orchestrator",
            kind="shutdown",
            data=Shutdown(kind="shutdown", reason="stop"),
        )


def test_dst_optional_for_agent_to_orchestrator_frames() -> None:
    env = Envelope(
        v=1,
        ts=datetime(2026, 6, 28, 19, 0, 0, tzinfo=UTC),
        seq=0,
        src="agent0",
        kind="harness_exit",
        data=HarnessExit(kind="harness_exit", reason="clean", code=0, last_turn=1),
    )

    assert env.dst is None


def test_parse_envelope_rejects_oversized_frame() -> None:
    big = "x" * (MAX_FRAME_BYTES + 1)
    line = json.dumps(
        {
            "v": 1,
            "ts": "2026-06-28T19:00:00.123456Z",
            "seq": 0,
            "src": "agent0",
            "kind": "bash_result",
            "data": {
                "kind": "bash_result",
                "turn": 1,
                "request_id": "r1",
                "terminal_output": big,
                "duration_s": 0.1,
            },
        }
    )

    with pytest.raises(ValueError, match="frame exceeds"):
        parse_envelope(line)


def test_serialize_envelope_rejects_oversized_payload() -> None:
    big_payload = "x" * (MAX_FRAME_BYTES + 1)
    env = Envelope(
        v=1,
        ts=datetime(2026, 6, 28, 19, 0, 0, tzinfo=UTC),
        seq=0,
        src="agent0",
        kind="bash_result",
        data=BashResult(
            kind="bash_result",
            turn=1,
            request_id="r1",
            terminal_output=big_payload,
            duration_s=0.1,
        ),
    )

    with pytest.raises(ValueError, match="serialized frame exceeds"):
        serialize_envelope(env)
