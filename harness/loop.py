from __future__ import annotations

import getpass
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from common import protocol as proto
from common.clock import now_utc
from common.errors import TransportError
from harness import _loop_helpers as helpers
from harness import attempt_logging, config, heartbeat, llm, parser, shell
from harness.chat import Chat
from harness.transport import Transport

_SUMMARY_THRESHOLD = 8_000
_TRUNCATE_BYTES = 10_240
_ID_BYTES = 8
_POLL_SLICE_S = 0.01
_CAPABILITY_TIMEOUT_S = 30.0
_RESERVATION_TIMEOUT_S = 10.0
_BUDGET_CAPABILITY_VERSION = 1
_LOG = logging.getLogger(__name__)


class _ShutdownRequested(Exception):
    pass


@dataclass(slots=True)
class _State:
    src: str
    transport: Transport
    seq: int = 0
    turn: int = 0
    pending_complete: bool = False
    budget_enabled: bool = False

    def emit(self, data: proto.Frame) -> None:
        envelope = proto.Envelope(
            v=1,
            ts=now_utc(),
            seq=self.seq,
            src=self.src,
            dst="orchestrator",
            kind=data.kind,
            data=data,
        )
        proto.serialize_envelope(envelope)
        self.transport.send(envelope)
        self.seq += 1

    def exit(
        self,
        reason: Literal["clean", "crash", "llm_fatal", "shutdown_received"],
        code: int,
    ) -> None:
        try:
            self.emit(proto.HarnessExit(reason=reason, code=code, last_turn=self.turn))
        except TransportError:
            _LOG.warning("failed to emit harness_exit")


def run_harness(
    *,
    config_path: Path,
    transport: Transport,
    initial_template: str,
    slot: int,
    base_dir: Path = Path(),
    max_turns: int = 1000,
    silence_threshold_s: float = 5.0,
) -> int:
    state = _State(f"agent{slot}", transport)
    try:
        cfg, api_key, chat = _setup(config_path, base_dir, initial_template, state)
        name = f"arenabench-{state.src}-{os.getpid()}-{uuid.uuid4().hex[:_ID_BYTES]}"
        with shell.TmuxShell(name, truncate_bytes=_TRUNCATE_BYTES) as tmux:
            while state.turn < max_turns:
                code = _process_pending(state, chat, silence_threshold_s)
                if code is not None:
                    return code
                parsed = _llm_turn(state, chat, tmux, cfg, api_key)
                if helpers.finish_turn(state, chat, parsed):
                    state.exit("clean", 0)
                    return 0
        state.exit("clean", 0)
        return 0
    except _ShutdownRequested:
        return 0
    except llm.LlmCallError:
        _LOG.error("harness loop hit fatal LLM error")
        state.exit("llm_fatal", 1)
        return 1
    except Exception:
        _LOG.exception("harness loop crashed")
        state.exit("crash", 1)
        return 1


def _setup(
    path: Path, base_dir: Path, initial_template: str, state: _State
) -> tuple[config.AgentConfig, str | None, Chat]:
    cfg = config.load_config(path)
    api_key = config.resolve_api_key(cfg)
    system = config.read_system_prompt(cfg, base_dir)
    chat = Chat(
        f"{system}\n\n{initial_template}",
        cfg.model,
        cfg.resolved_context_tokens,
        _SUMMARY_THRESHOLD,
        _TRUNCATE_BYTES,
    )
    state.emit(
        proto.PidAnnounce(
            pid=os.getpid(),
            user=getpass.getuser(),
            uid=os.getuid(),
            hostname=socket.gethostname(),
            parser=cfg.parser,
            model=cfg.model,
            budget_capability_version=_BUDGET_CAPABILITY_VERSION,
        )
    )
    deadline = time.monotonic() + _CAPABILITY_TIMEOUT_S
    while True:
        capability = state.transport.recv(timeout_s=max(0.0, deadline - time.monotonic()))
        if capability is None:
            raise llm.LlmCallError("budget capability negotiation timeout or channel close")
        if isinstance(capability.data, proto.BudgetCapability):
            state.budget_enabled = capability.data.enabled
            break
        if _handle_inbound(capability, chat, state) is not None:
            raise _ShutdownRequested
    return cfg, api_key, chat


def _process_pending(state: _State, chat: Chat, silence_threshold_s: float) -> int | None:
    env = state.transport.recv(timeout_s=min(silence_threshold_s, _POLL_SLICE_S))
    while True:
        if env is None:
            return None
        code = _handle_inbound(env, chat, state)
        if code is not None:
            return code
        env = state.transport.recv(timeout_s=0)


def _handle_inbound(env: proto.Envelope, chat: Chat, state: _State) -> int | None:
    if isinstance(env.data, proto.HeartbeatTick):
        payload = heartbeat.inject(chat, env.data.elapsed_s, state.turn)
        state.emit(
            proto.HeartbeatInjected(turn=state.turn, elapsed_s=env.data.elapsed_s, payload=payload)
        )
    elif isinstance(env.data, proto.Shutdown):
        state.emit(proto.HarnessExit(reason="shutdown_received", code=0, last_turn=state.turn))
        return 0
    return None


def _llm_turn(
    state: _State, chat: Chat, tmux: shell.TmuxShell, cfg: config.AgentConfig, api_key: str | None
) -> parser.ParsedResponse | None:
    if cfg.mock_raise_on_turn is not None and state.turn >= cfg.mock_raise_on_turn:
        raise RuntimeError(
            f"mock_raise_on_turn={cfg.mock_raise_on_turn} simulated crash at turn {state.turn}"
        )
    if chat.history and chat.history[-1].role == "assistant":
        chat.append_user("Continue.")
    chat.trim_to_fit()
    request_id = f"llm-{state.turn}-{uuid.uuid4().hex[:_ID_BYTES]}"
    messages = helpers.chat_history_for_litellm(chat)
    prompt_chars = sum(len(message["content"]) for message in messages)
    last_excerpt = attempt_logging.last_user_excerpt(messages)
    prompt_tokens = chat.prompt_tokens
    current_attempt = 0

    def _emit_attempt(attempt: int) -> None:
        nonlocal current_attempt
        current_attempt = attempt
        identity = attempt_logging.AttemptIdentity(state.turn, request_id, attempt)
        # Plan §9 S16: one llm_request per attempt (same request_id), so a
        # retry on 429 / 5xx / timeout is visible in api.jsonl as two frames
        # for the same turn rather than being collapsed inside the harness.
        state.emit(
            proto.LlmRequest(
                turn=state.turn,
                request_id=request_id,
                model=cfg.model,
                messages_count=len(messages),
                prompt_chars=prompt_chars,
                temperature=cfg.temperature,
                last_user_excerpt=last_excerpt,
                attempt=attempt,
                prompt_tokens=prompt_tokens,
                max_output_tokens=cfg.max_output_tokens,
                fallback_models=cfg.fallbacks,
            )
        )
        if state.budget_enabled:
            deadline = time.monotonic() + _RESERVATION_TIMEOUT_S
            while True:
                decision_env = state.transport.recv(timeout_s=max(0.0, deadline - time.monotonic()))
                if decision_env is None:
                    raise attempt_logging.reservation_error(
                        identity, "reservation decision timeout or channel close"
                    )
                decision = decision_env.data
                if isinstance(decision, proto.LlmReservationDecision):
                    break
                if _handle_inbound(decision_env, chat, state) is not None:
                    raise _ShutdownRequested
            if decision.request_id != request_id or decision.attempt != attempt:
                raise attempt_logging.reservation_error(
                    identity, "reservation decision correlation mismatch"
                )
            if not decision.granted:
                raise attempt_logging.reservation_error(
                    identity, f"reservation denied: {decision.reason}"
                )
        if not attempt_logging.emit_context(state.emit, identity, messages):
            _LOG.warning("failed to emit context_logging_error")

    def _emit_failure(error: llm.LlmCallError) -> None:
        identity = attempt_logging.AttemptIdentity(
            state.turn, request_id, error.attempt if error.attempt is not None else current_attempt
        )
        try:
            state.emit(attempt_logging.failure_frame(identity, error))
        except (TransportError, ValueError):
            _LOG.warning("failed to emit llm_attempt_failure")

    try:
        result = llm.call(
            model=cfg.model,
            messages=messages,
            temperature=cfg.temperature,
            max_tokens=cfg.max_output_tokens,
            timeout_s=float(cfg.request_timeout_s),
            num_retries=cfg.num_retries,
            fallbacks=cfg.fallbacks,
            reasoning_effort=cfg.reasoning_effort,
            api_mode=cfg.api_mode,
            api_key=api_key,
            mock_response=cfg.mock_response,
            on_attempt=_emit_attempt,
            on_failure=_emit_failure,
        )
    except llm.LlmCallError as exc:
        identity = attempt_logging.AttemptIdentity(state.turn, request_id, current_attempt)
        state.emit(attempt_logging.failure_frame(identity, exc))
        raise
    parsed = helpers.parse_or_record_error(
        result, cfg.parser, state.turn, request_id, state.emit, chat
    )
    if parsed is None:
        return None
    chat.append_assistant(result.content)
    helpers.run_commands(parsed, state.turn, chat, tmux, state.emit, _ID_BYTES)
    return parsed
