from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, TextIO

from pydantic import BaseModel, ConfigDict, Field

from common.errors import LifecycleError
from common.ids import AgentSlot, MatchId, make_agent_slot
from common.protocol import Envelope, serialize_envelope

_AGENT_WIDTH = 2
_EMPTY = ""


class _SummaryShape(BaseModel):
    """Mirror of orchestrator/schemas/summary.schema.json for runtime guard.

    Validates the dict passed to MatchLogger.write_summary so callers cannot
    write a summary.json that violates the on-disk contract. Constraints
    must mirror summary.schema.json exactly; drift is asserted by
    tests/unit/test_summary_schema.py.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    result: Literal["victory", "draw", "timeout", "error"]
    winner: Annotated[int, Field(ge=0, le=15)] | None
    cause: Annotated[str, Field(min_length=1)]
    final_state: Literal["DONE"]


@dataclass(frozen=True, slots=True)
class _AgentFiles:
    bash: TextIO
    api: TextIO
    events: TextIO
    context: TextIO


class MatchLogger:
    def __init__(self, log_root: Path, match_id: MatchId, n_agents: int) -> None:
        self._log_root = log_root
        self._match_id = match_id
        self._match_dir = log_root / "matches" / str(match_id)
        self._match_dir.mkdir(parents=True, exist_ok=True)

        self._match_file = self._match_dir / "match.jsonl"
        self._orchestrator_log = self._match_dir / "orchestrator.log"
        self._match_handle = self._match_file.open("a", encoding="utf-8", buffering=1)
        self._orchestrator_handle = self._orchestrator_log.open("a", encoding="utf-8", buffering=1)
        self._agent_files: dict[AgentSlot, _AgentFiles] = {}
        self._handles: list[TextIO] = [self._match_handle, self._orchestrator_handle]

        agents_dir = self._match_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        for slot_index in range(n_agents):
            slot = make_agent_slot(slot_index)
            slot_dir = agents_dir / f"{slot_index:0{_AGENT_WIDTH}d}"
            slot_dir.mkdir(parents=True, exist_ok=True)
            files = _AgentFiles(
                bash=self._open_file(slot_dir / "bash.jsonl"),
                api=self._open_file(slot_dir / "api.jsonl"),
                events=self._open_file(slot_dir / "events.jsonl"),
                context=self._open_file(slot_dir / "context.jsonl"),
            )
            self._agent_files[slot] = files

    @property
    def match_dir(self) -> Path:
        return self._match_dir

    def _open_file(self, path: Path) -> TextIO:
        handle = path.open("a", encoding="utf-8", buffering=1)
        self._handles.append(handle)
        return handle

    def _agent_slot(self, src: str) -> AgentSlot:
        if not src.startswith("agent"):
            raise LifecycleError("unknown logger source", state=src, event="route")
        slot_text = src.removeprefix("agent")
        if not slot_text.isdigit():
            raise LifecycleError("invalid agent source", state=src, event=slot_text)
        return make_agent_slot(int(slot_text))

    def _file_for_envelope(self, env: Envelope) -> TextIO:
        if env.src in {"orchestrator", "guest-probe"}:
            return self._match_handle
        slot = self._agent_slot(env.src)
        files = self._agent_files.get(slot)
        if files is None:
            raise LifecycleError("unknown logger source", state=env.src, event=str(env.data.kind))
        kind = env.data.kind
        if kind in {"pid_announce", "bash_request", "bash_result"}:
            return files.bash
        if kind in {"llm_request", "llm_response", "heartbeat_injected"}:
            return files.api
        if kind in {"turn_summary", "harness_exit"}:
            return files.events
        raise LifecycleError("unknown logger kind", state=env.src, event=str(kind))

    def write_envelope(self, env: Envelope) -> None:
        handle = self._file_for_envelope(env)
        handle.write(f"{serialize_envelope(env)}\n")

    def write_summary(self, summary: dict[str, object]) -> None:
        _SummaryShape.model_validate(summary)
        summary_path = self._match_dir / "summary.json"
        summary_json = json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False)
        summary_path.write_text(summary_json, encoding="utf-8")

    def close(self) -> None:
        for handle in self._handles:
            handle.flush()
            handle.close()

    def __enter__(self) -> MatchLogger:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
