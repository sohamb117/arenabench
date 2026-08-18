from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import cast

import pytest

from orchestrator.transcript import TranscriptDocument, TranscriptError, read_transcript
from orchestrator.transcript_render import render_text
from tests.unit.transcript_fixtures import (
    JsonRecord,
    envelope,
    exact_run,
    legacy_run,
    write_jsonl,
)

_TOTAL_TOKENS = 10
_RESERVED_NANO_USD = 2_500_000
_AGENT_ZERO_RESERVED = 100
_AGENT_ONE_RESERVED = 200


def test_exact_success_preserves_unicode_and_correlates_attempt(tmp_path: Path) -> None:
    run = exact_run(tmp_path)

    document = read_transcript(run)

    attempt = document.agents[0].turns[0].attempts[0]
    assert attempt.context.kind == "context"
    assert attempt.context.messages[0].content == "秘密\nexact"
    assert attempt.outcome.kind == "reply"
    assert attempt.outcome.content == "原始 reply\nline"
    result = attempt.commands[0].result
    usage = attempt.usage
    reservation = attempt.reservation
    assert result is not None
    assert usage is not None
    assert reservation is not None
    assert result.terminal_output == "\u001b[31m✓\u001b[0m\n"
    assert usage.total_tokens == _TOTAL_TOKENS
    assert reservation.reserved_nano_usd == _RESERVED_NANO_USD
    assert TranscriptDocument.model_validate_json(document.model_dump_json()) == document


def test_provider_failure_and_retry_are_distinct_attempts(tmp_path: Path) -> None:
    run = exact_run(tmp_path)
    context_path = run / "agents" / "00" / "context.jsonl"
    records: list[JsonRecord] = []
    for attempt in (0, 1):
        context_frame: JsonRecord = {
            "turn": 3,
            "request_id": "retry",
            "attempt": attempt,
            "messages": [{"role": "user", "content": "x"}],
        }
        records.append(
            envelope(
                "2026-08-17T12:00:00Z",
                "agent0",
                "llm_context_snapshot",
                context_frame,
            )
        )
    failure: JsonRecord = {
        "turn": 3,
        "request_id": "retry",
        "attempt": 0,
        "category": "provider_refusal",
        "finish_reason": "content_filter",
        "error_text": "refused",
        "error_class": "ProviderRefusal",
        "status_code": None,
        "prompt_tokens": 4,
        "completion_tokens": 0,
        "total_tokens": 4,
        "latency_s": 0.5,
    }
    records.insert(
        1,
        envelope("2026-08-17T12:00:01Z", "agent0", "llm_attempt_failure", failure),
    )
    write_jsonl(context_path, records)
    requests: list[JsonRecord] = []
    for attempt in (0, 1):
        request: JsonRecord = {
            "turn": 3,
            "request_id": "retry",
            "attempt": attempt,
            "model": "m",
            "messages_count": 1,
            "prompt_chars": 1,
            "temperature": 0.0,
        }
        requests.append(envelope("2026-08-17T12:00:00Z", "agent0", "llm_request", request))
    response: JsonRecord = {
        "turn": 3,
        "request_id": "retry",
        "attempt": 1,
        "content": '{"commands":[]}',
        "parser": "json",
        "parse_ok": True,
        "parsed": {"commands": []},
        "prompt_tokens": 4,
        "completion_tokens": 2,
        "total_tokens": 6,
        "cost_usd": 0.001,
        "latency_s": 0.4,
        "error": None,
    }
    requests.append(envelope("2026-08-17T12:00:02Z", "agent0", "llm_response", response))
    write_jsonl(run / "agents" / "00" / "api.jsonl", requests)
    bash_request: JsonRecord = {
        "turn": 3,
        "request_id": "retry-bash",
        "commands": [{"keystrokes": "id", "duration_sec": 0.1, "is_blocking": True}],
    }
    write_jsonl(
        run / "agents" / "00" / "bash.jsonl",
        [envelope("2026-08-17T12:00:03Z", "agent0", "bash_request", bash_request)],
    )

    turn = read_transcript(run, turn=3).agents[0].turns[0]

    assert [attempt.attempt for attempt in turn.attempts] == [0, 1]
    assert turn.attempts[0].outcome.kind == "failure"
    assert turn.attempts[0].outcome.finish_reason == "content_filter"
    assert turn.attempts[0].commands == ()
    assert turn.attempts[1].outcome.kind == "reply"
    assert turn.attempts[1].commands[0].request_id == "retry-bash"


def test_filters_select_agent_and_turn(tmp_path: Path) -> None:
    run = exact_run(tmp_path)

    document = read_transcript(run, agent=0, turn=2)

    assert [agent.slot for agent in document.agents] == [0]
    assert [turn.turn for turn in document.agents[0].turns] == [2]


def test_reservations_are_correlated_by_agent_slot(tmp_path: Path) -> None:
    run = exact_run(tmp_path)
    agent_one = run / "agents" / "01"
    shutil.copytree(run / "agents" / "00", agent_one)
    for path in agent_one.glob("*.jsonl"):
        path.write_text(
            path.read_text(encoding="utf-8").replace('"src": "agent0"', '"src": "agent1"'),
            encoding="utf-8",
        )
    decisions = [
        envelope(
            "2026-08-17T12:00:01Z",
            "orchestrator",
            "llm_reservation_decision",
            {
                "request_id": "req-unicode",
                "attempt": 0,
                "granted": True,
                "reason": "granted",
                "reserved_nano_usd": reserved,
            },
        )
        for reserved in (_AGENT_ZERO_RESERVED, _AGENT_ONE_RESERVED)
    ]
    decisions[0]["dst"] = "agent0"
    decisions[1]["dst"] = "agent1"
    write_jsonl(run / "match.jsonl", decisions)

    document = read_transcript(run)

    agent_zero = document.agents[0].turns[0].attempts[0].reservation
    agent_one = document.agents[1].turns[0].attempts[0].reservation
    assert agent_zero is not None
    assert agent_one is not None
    assert agent_zero.reserved_nano_usd == _AGENT_ZERO_RESERVED
    assert agent_one.reserved_nano_usd == _AGENT_ONE_RESERVED


def test_legacy_defaults_latest_epoch_and_supports_explicit_epoch(tmp_path: Path) -> None:
    run = legacy_run(tmp_path)

    latest = read_transcript(run)
    first = read_transcript(run, legacy_run=0)

    assert latest.run.legacy_run == 1
    assert latest.agents[0].turns[0].attempts[0].request.request_id == "new"
    assert first.agents[0].turns[0].attempts[0].request.request_id == "old"
    text = render_text(latest)
    assert "[context unavailable: legacy log]" in text
    assert "[provider outcome unavailable: legacy log]" in text
    assert "COMMAND request_id=legacy-bash" in text
    assert "agent1\n" in text
    assert "content_filter" not in text


@pytest.mark.parametrize("case", ["missing", "file", "missing_agent", "malformed", "oversized"])
def test_reader_rejects_invalid_input(tmp_path: Path, case: str) -> None:
    run = exact_run(tmp_path)
    path = run
    agent = None
    if case == "missing":
        path = tmp_path / "absent"
    elif case == "file":
        path = run / "run.json"
    elif case == "missing_agent":
        agent = 9
    elif case == "malformed":
        (run / "agents" / "00" / "api.jsonl").write_text("{bad}\n", encoding="utf-8")
    elif case == "oversized":
        (run / "agents" / "00" / "api.jsonl").write_text("{" + "x" * 1_100_000, encoding="utf-8")

    with pytest.raises(TranscriptError):
        read_transcript(path, agent=agent)


def test_json_document_is_machine_parseable(tmp_path: Path) -> None:
    document = read_transcript(exact_run(tmp_path))

    payload = cast(dict[str, object], json.loads(document.model_dump_json()))

    assert payload["schema_version"] == 1
    assert '"content":"原始 reply\\nline"' in document.model_dump_json()
