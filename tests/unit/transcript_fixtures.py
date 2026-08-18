from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import JsonValue

type JsonRecord = dict[str, JsonValue]


def write_jsonl(path: Path, records: Sequence[Mapping[str, JsonValue]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(record, ensure_ascii=False)}\n" for record in records),
        encoding="utf-8",
    )


def envelope(ts: str, src: str, kind: str, data: JsonRecord) -> JsonRecord:
    return {
        "v": 1,
        "ts": ts,
        "seq": 1,
        "src": src,
        "dst": "orchestrator",
        "kind": kind,
        "data": data,
    }


def exact_run(base: Path) -> Path:
    run = base / "exact"
    (run / "agents" / "00").mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "match_id": "exact",
        "run_id": "20260817T120000000000000Z-abcdef123456",
        "started_at": "2026-08-17T12:00:00Z",
        "n_agents": 1,
    }
    (run / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
    ts = "2026-08-17T12:00:01Z"
    reservation: JsonRecord = {
        "request_id": "req-unicode",
        "attempt": 0,
        "granted": True,
        "reason": "granted",
        "reserved_nano_usd": 2_500_000,
    }
    reservation_envelope = envelope(ts, "orchestrator", "llm_reservation_decision", reservation)
    reservation_envelope["dst"] = "agent0"
    write_jsonl(run / "match.jsonl", [reservation_envelope])
    agent = run / "agents" / "00"
    context: JsonRecord = {
        "turn": 2,
        "request_id": "req-unicode",
        "attempt": 0,
        "messages": [
            {"role": "system", "content": "秘密\nexact"},
            {"role": "user", "content": "继续"},
        ],
    }
    write_jsonl(
        agent / "context.jsonl",
        [envelope(ts, "agent0", "llm_context_snapshot", context)],
    )
    request: JsonRecord = {
        "turn": 2,
        "request_id": "req-unicode",
        "attempt": 0,
        "model": "provider/model",
        "messages_count": 2,
        "prompt_chars": 10,
        "temperature": 0.0,
        "prompt_tokens": 7,
        "max_output_tokens": 20,
        "fallback_models": None,
        "last_user_excerpt": "继续",
    }
    response: JsonRecord = {
        "turn": 2,
        "request_id": "req-unicode",
        "attempt": 0,
        "content": "原始 reply\nline",
        "parser": "json",
        "parse_ok": True,
        "parsed": {
            "commands": [{"keystrokes": "printf '✓'", "duration_sec": 0.1, "is_blocking": True}]
        },
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
        "cost_usd": 0.0004,
        "latency_s": 1.25,
        "error": None,
    }
    write_jsonl(
        agent / "api.jsonl",
        [
            envelope(ts, "agent0", "llm_request", request),
            envelope(ts, "agent0", "llm_response", response),
        ],
    )
    bash_request: JsonRecord = {
        "turn": 2,
        "request_id": "bash-1",
        "commands": [{"keystrokes": "printf '✓'", "duration_sec": 0.1, "is_blocking": True}],
    }
    bash_result: JsonRecord = {
        "turn": 2,
        "request_id": "bash-1",
        "terminal_output": "\u001b[31m✓\u001b[0m\n",
        "truncated_bytes": 0,
        "exit_status": 0,
        "duration_s": 0.12,
    }
    write_jsonl(
        agent / "bash.jsonl",
        [
            envelope(ts, "agent0", "bash_request", bash_request),
            envelope(ts, "agent0", "bash_result", bash_result),
        ],
    )
    summary: JsonRecord = {
        "turn": 2,
        "action_count": 1,
        "free_tokens": 90,
        "summarized": False,
        "history_chars": 42,
    }
    write_jsonl(
        agent / "events.jsonl",
        [envelope(ts, "agent0", "turn_summary", summary)],
    )
    return run


def legacy_run(base: Path) -> Path:
    run = base / "legacy"
    agent = run / "agents" / "01"
    agent.mkdir(parents=True)
    starts = ["2026-08-17T01:00:00Z", "2026-08-17T02:00:00Z"]
    state: JsonRecord = {
        "from_state": "IDLE",
        "to_state": "VM_BOOTING",
        "reason": "run",
    }
    write_jsonl(
        run / "match.jsonl",
        [envelope(ts, "orchestrator", "match_state_change", state) for ts in starts],
    )
    requests: list[JsonRecord] = []
    for ts, request_id in zip(starts, ("old", "new"), strict=True):
        data: JsonRecord = {
            "turn": 0,
            "request_id": request_id,
            "attempt": 0,
            "model": "m",
            "messages_count": 1,
            "prompt_chars": 1,
            "temperature": 0.0,
        }
        requests.append(envelope(ts, "agent1", "llm_request", data))
    write_jsonl(agent / "api.jsonl", requests)
    bash_request: JsonRecord = {
        "turn": 0,
        "request_id": "legacy-bash",
        "commands": [{"keystrokes": "whoami", "duration_sec": 0.1, "is_blocking": True}],
    }
    bash_result: JsonRecord = {
        "turn": 0,
        "request_id": "legacy-bash",
        "terminal_output": "agent1\n",
        "truncated_bytes": 0,
        "exit_status": 0,
        "duration_s": 0.2,
    }
    write_jsonl(
        agent / "bash.jsonl",
        [
            envelope(starts[1], "agent1", "bash_request", bash_request),
            envelope(starts[1], "agent1", "bash_result", bash_result),
        ],
    )
    for name in ("events.jsonl", "context.jsonl"):
        (agent / name).write_text("", encoding="utf-8")
    return run
