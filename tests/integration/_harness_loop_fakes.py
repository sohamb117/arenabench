from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, cast

import pytest

import harness.llm
import harness.loop
from common.clock import now_utc
from common.protocol import BudgetCapability, Envelope, Frame
from harness.llm import LlmCallResult
from harness.shell import CommandResult
from harness.transport_fake import InMemoryTransport

SLOT = 2
EXIT_TIMEOUT_S = 3.0
RECV_TIMEOUT_S = 2.0
SHORT_TIMEOUT_S = 0.2
PROMPT_TOKENS = 10
COMPLETION_TOKENS = 5
TOTAL_TOKENS = 15
LATENCY_S = 0.1


@dataclass(frozen=True, slots=True)
class RunResult:
    code: int | None = None
    error: BaseException | None = None


@dataclass(frozen=True, slots=True)
class HarnessRun:
    peer: InMemoryTransport
    result: dict[str, RunResult]
    thread: threading.Thread
    captured_messages: list[list[dict[str, str]]]


class FakeShell:
    calls: ClassVar[list[str]] = []

    def __init__(self, session_name: str, *, truncate_bytes: int = 10_240) -> None:
        self.session_name = session_name
        self.truncate_bytes = truncate_bytes

    def __enter__(self) -> FakeShell:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def run(self, keystrokes: str, *, duration_sec: float, is_blocking: bool) -> CommandResult:
        self.calls.append(keystrokes)
        return CommandResult(
            keystrokes=keystrokes,
            duration_s=duration_sec,
            terminal_output=f"ran: {keystrokes}",
            truncated_bytes=0,
            exit_status=0 if is_blocking else None,
        )


def make_response(*, command: str | None = None, task_complete: bool = False) -> LlmCallResult:
    commands = (
        []
        if command is None
        else [{"keystrokes": command, "duration_sec": 0.0, "is_blocking": True}]
    )
    content = json.dumps(
        {"analysis": "a", "plan": "p", "commands": commands, "task_complete": task_complete}
    )
    return LlmCallResult(
        content=content,
        prompt_tokens=PROMPT_TOKENS,
        completion_tokens=COMPLETION_TOKENS,
        total_tokens=TOTAL_TOKENS,
        cost_usd=None,
        latency_s=LATENCY_S,
        error=None,
    )


def write_config(tmp_path: Path, *, max_tokens: int = 10_000) -> Path:
    prompt = tmp_path / "system.txt"
    prompt.write_text("system prompt", encoding="utf-8")
    config = tmp_path / "agent.json"
    config.write_text(
        json.dumps(
            {
                "model": "test/model",
                "temperature": 0.1,
                "max_tokens": max_tokens,
                "request_timeout_s": 1,
                "num_retries": 1,
                "fallbacks": None,
                "parser": "json",
                "api_key_env": "TEST_API_KEY",
                "system_prompt_path": "system.txt",
            }
        ),
        encoding="utf-8",
    )
    return config


def run_harness_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    responses: Sequence[LlmCallResult | BaseException],
    *,
    max_turns: int = 10,
    max_tokens: int = 10_000,
    budget_enabled: bool = False,
    send_capability: bool = True,
) -> HarnessRun:
    transport = InMemoryTransport()
    result: dict[str, RunResult] = {}
    captured_messages: list[list[dict[str, str]]] = []
    calls = iter(responses)
    peer = transport.peer()
    if send_capability:
        send(peer, BudgetCapability(version=1, enabled=budget_enabled))

    def fake_call(**kwargs: object) -> LlmCallResult:
        on_attempt = kwargs.get("on_attempt")
        if on_attempt is not None:
            cast(Callable[[int], None], on_attempt)(0)
        messages_obj = kwargs.get("messages")
        if isinstance(messages_obj, list):
            snapshot: list[dict[str, str]] = []
            for raw in cast(list[object], messages_obj):
                if isinstance(raw, dict):
                    typed = cast(dict[object, object], raw)
                    snapshot.append({str(k): str(v) for k, v in typed.items()})
            captured_messages.append(snapshot)
        next_result = next(calls)
        if isinstance(next_result, BaseException):
            raise next_result
        return next_result

    monkeypatch.setattr(harness.llm, "call", fake_call)
    monkeypatch.setattr(harness.loop, "_captured_messages", captured_messages, raising=False)

    def target() -> None:
        try:
            result["value"] = RunResult(
                code=harness.loop.run_harness(
                    config_path=write_config(tmp_path, max_tokens=max_tokens),
                    transport=transport,
                    initial_template="initial task",
                    slot=SLOT,
                    base_dir=tmp_path,
                    max_turns=max_turns,
                    silence_threshold_s=SHORT_TIMEOUT_S,
                )
            )
        except BaseException as exc:
            result["value"] = RunResult(error=exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return HarnessRun(peer=peer, result=result, thread=thread, captured_messages=captured_messages)


def recv(peer: InMemoryTransport) -> Envelope:
    env = peer.recv(timeout_s=RECV_TIMEOUT_S)
    assert env is not None
    return env


def send(peer: InMemoryTransport, data: Frame, *, seq: int = 0) -> None:
    peer.send(
        Envelope(
            v=1,
            ts=now_utc(),
            seq=seq,
            src="orchestrator",
            dst=f"agent{SLOT}",
            kind=data.kind,
            data=data,
        )
    )


def drain_until_exit(run: HarnessRun) -> list[Envelope]:
    frames: list[Envelope] = []
    while True:
        env = recv(run.peer)
        frames.append(env)
        if env.kind == "harness_exit":
            run.thread.join(timeout=EXIT_TIMEOUT_S)
            assert not run.thread.is_alive()
            assert run.result["value"].error is None
            return frames


def two_complete() -> list[LlmCallResult | BaseException]:
    return [make_response(task_complete=True), make_response(task_complete=True)]
