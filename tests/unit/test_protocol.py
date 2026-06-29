import json
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from common.protocol import (
    BashCommand,
    BashRequest,
    BashResult,
    BootAck,
    BootAckResponse,
    Envelope,
    Frame,
    HarnessExit,
    HeartbeatInjected,
    HeartbeatTick,
    Kill0,
    Kill0Response,
    LlmRequest,
    LlmResponse,
    PidAnnounce,
    ProcList,
    ProcListResponse,
    Shutdown,
    TurnSummary,
    parse_envelope,
    serialize_envelope,
)

MICROSECOND = 123456


def envelope_for(kind: str, data: Frame, seq: int = 42) -> Envelope:
    return Envelope(
        v=1,
        ts=datetime(2026, 6, 28, 19, 0, 0, MICROSECOND, tzinfo=UTC),
        seq=seq,
        src="agent0",
        dst="orchestrator",
        kind=kind,
        data=data,
    )


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        (
            "pid_announce",
            PidAnnounce(
                kind="pid_announce",
                pid=1001,
                user="agent1",
                uid=1001,
                hostname="vm1",
                parser="json",
                model="gpt-4o",
            ),
        ),
        (
            "bash_request",
            BashRequest(
                kind="bash_request",
                turn=1,
                request_id="r1",
                commands=[BashCommand(keystrokes="id\n", duration_sec=0.1, is_blocking=True)],
            ),
        ),
        (
            "bash_result",
            BashResult(
                kind="bash_result",
                turn=1,
                request_id="r1",
                terminal_output="uid=1001(agent1)\n",
                truncated_bytes=0,
                exit_status=0,
                duration_s=0.2,
            ),
        ),
        (
            "llm_request",
            LlmRequest(
                kind="llm_request",
                turn=2,
                request_id="llm1",
                model="gpt-4o",
                messages_count=4,
                prompt_chars=512,
                temperature=0.2,
            ),
        ),
        (
            "llm_response",
            LlmResponse(
                kind="llm_response",
                turn=2,
                request_id="llm1",
                content="ok",
                parser="json",
                parse_ok=True,
                parsed={"answer": "ok"},
                prompt_tokens=10,
                completion_tokens=2,
                total_tokens=12,
                cost_usd=0.01,
                latency_s=0.3,
                error=None,
            ),
        ),
        (
            "heartbeat_injected",
            HeartbeatInjected(kind="heartbeat_injected", turn=3, elapsed_s=120.5, payload="tick"),
        ),
        (
            "turn_summary",
            TurnSummary(
                kind="turn_summary",
                turn=3,
                action_count=2,
                free_tokens=128,
                summarized=True,
                history_chars=2048,
            ),
        ),
        (
            "harness_exit",
            HarnessExit(kind="harness_exit", reason="clean", code=0, last_turn=9),
        ),
        (
            "heartbeat_tick",
            HeartbeatTick(kind="heartbeat_tick", elapsed_s=60.0, turn_hint=4),
        ),
        ("shutdown", Shutdown(kind="shutdown", reason="maintenance")),
        ("kill0", Kill0(kind="kill0", request_id="k1", pid=2001)),
        (
            "kill0_response",
            Kill0Response(kind="kill0_response", request_id="k1", pid=2001, alive=False),
        ),
        ("proc_list", ProcList(kind="proc_list", request_id="p1", user="agent1")),
        (
            "proc_list_response",
            ProcListResponse(
                kind="proc_list_response",
                request_id="p1",
                user="agent1",
                pids=[1, 2, 3],
            ),
        ),
        ("boot_ack", BootAck(kind="boot_ack", request_id="b1")),
        (
            "boot_ack_response",
            BootAckResponse(
                kind="boot_ack_response",
                request_id="b1",
                kernel="6.1.0",
                uptime_s=12.5,
                cid=3,
            ),
        ),
    ],
)
def test_round_trip_every_frame(kind: str, data: Frame) -> None:
    given = envelope_for(kind, data)
    when = serialize_envelope(given)
    then = parse_envelope(when)
    assert then == given


def test_serialize_lifts_kind_and_omits_payload_kind() -> None:
    env = envelope_for(
        "shutdown",
        Shutdown(kind="shutdown", reason="stop"),
    )

    wire = TypeAdapter(dict[str, object]).validate_json(serialize_envelope(env))

    assert wire["kind"] == "shutdown"
    assert wire["data"] == {"reason": "stop"}


def test_bad_kind_raises_validation_error() -> None:
    line = json.dumps(
        {
            "v": 1,
            "ts": "2026-06-28T19:00:00.123456Z",
            "seq": 0,
            "src": "agent0",
            "kind": "nope",
            "data": {},
        }
    )

    with pytest.raises(ValidationError):
        parse_envelope(line)


def test_missing_required_field_raises_validation_error() -> None:
    line = json.dumps(
        {
            "v": 1,
            "ts": "2026-06-28T19:00:00.123456Z",
            "seq": 0,
            "src": "agent0",
            "kind": "boot_ack_response",
            "data": {
                "kind": "boot_ack_response",
                "request_id": "b1",
                "kernel": "6.1.0",
                "uptime_s": 12.5,
            },
        }
    )

    with pytest.raises(ValidationError):
        parse_envelope(line)


def test_ts_accepts_microsecond_precision() -> None:
    env = parse_envelope(
        json.dumps(
            {
                "v": 1,
                "ts": "2026-06-28T19:00:00.123456Z",
                "seq": 1,
                "src": "agent0",
                "kind": "shutdown",
                "data": {"reason": "stop"},
            }
        )
    )

    assert env.ts.microsecond == MICROSECOND


def test_negative_seq_rejected() -> None:
    with pytest.raises(ValidationError):
        Envelope(
            v=1,
            ts=datetime(2026, 6, 28, 19, 0, 0, tzinfo=UTC),
            seq=-1,
            src="agent0",
            kind="shutdown",
            data=Shutdown(kind="shutdown", reason="stop"),
        )
