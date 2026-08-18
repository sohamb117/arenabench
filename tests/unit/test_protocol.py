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
    HarnessDead,
    HarnessExit,
    HeartbeatInjected,
    HeartbeatTick,
    Kill0,
    Kill0Response,
    LlmRequest,
    LlmResponse,
    MatchStateChange,
    MatchTerminated,
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
                turn=1,
                request_id="r1",
                commands=[BashCommand(keystrokes="id\n", duration_sec=0.1, is_blocking=True)],
            ),
        ),
        (
            "bash_result",
            BashResult(
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
                parse_error=None,
            ),
        ),
        (
            "heartbeat_injected",
            HeartbeatInjected(kind="heartbeat_injected", turn=3, elapsed_s=120.5, payload="tick"),
        ),
        (
            "turn_summary",
            TurnSummary(
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
                request_id="p1",
                user="agent1",
                pids=[1, 2, 3],
            ),
        ),
        ("boot_ack", BootAck(kind="boot_ack", request_id="b1")),
        (
            "boot_ack_response",
            BootAckResponse(
                request_id="b1",
                kernel="6.1.0",
                uptime_s=12.5,
                cid=3,
            ),
        ),
        (
            "harness_dead",
            HarnessDead(kind="harness_dead", slot=0, cause="vsock_disconnect+kill0_dead"),
        ),
        (
            "match_state_change",
            MatchStateChange(
                from_state="IN_MATCH",
                to_state="WINNER_GRACE",
                reason="alive_count==1",
            ),
        ),
        (
            "match_terminated",
            MatchTerminated(
                result="victory",
                winner=1,
                cause="opponent_crashed",
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


def test_llm_response_serializes_parse_error_under_legacy_wire_key() -> None:
    response = LlmResponse(
        turn=2,
        request_id="llm1",
        content="bad",
        parser="json",
        parse_ok=False,
        prompt_tokens=10,
        completion_tokens=2,
        total_tokens=12,
        latency_s=0.3,
        parse_error="invalid JSON",
    )

    wire = serialize_envelope(envelope_for("llm_response", response))

    assert '"error":"invalid JSON"' in wire
    assert '"parse_error"' not in wire


@pytest.mark.parametrize(
    "data_update",
    [
        {"parse_error": "invalid JSON"},
        {"error": "invalid JSON", "parse_error": "duplicate"},
    ],
)
def test_llm_response_rejects_python_only_parse_error_on_wire(
    data_update: dict[str, str],
) -> None:
    line = json.dumps(
        {
            "v": 1,
            "ts": "2026-06-28T19:00:00.123456Z",
            "seq": 1,
            "src": "agent0",
            "dst": "orchestrator",
            "kind": "llm_response",
            "data": {
                "turn": 2,
                "request_id": "llm1",
                "content": "bad",
                "parser": "json",
                "parse_ok": False,
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
                "latency_s": 0.3,
                **data_update,
            },
        }
    )

    with pytest.raises(ValueError, match="parse_error is not a wire field"):
        parse_envelope(line)


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
                "src": "orchestrator",
                "dst": "agent0",
                "kind": "shutdown",
                "data": {"reason": "stop"},
            }
        )
    )

    assert env.ts.microsecond == MICROSECOND
