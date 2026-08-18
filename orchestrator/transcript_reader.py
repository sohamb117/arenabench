from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from heapq import merge
from operator import attrgetter
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from orchestrator.run_layout import RunManifest
from orchestrator.transcript import (
    Agent,
    HarnessExit,
    Reservation,
    RunInfo,
    TranscriptDocument,
    TranscriptError,
    TranscriptFilters,
)
from orchestrator.transcript_assembly import AssemblyInput, AttemptKey, build_turns
from orchestrator.transcript_frames import (
    BashRequestFrame,
    BashResultFrame,
    ContextChunkFrame,
    ContextFrame,
    FailureFrame,
    HarnessExitFrame,
    RequestFrame,
    ReservationFrame,
    ResponseFrame,
    StateChangeFrame,
    TurnSummaryFrame,
)
from orchestrator.transcript_jsonl import FrameBudget, epoch_lines, read_jsonl

_AGENT_NAME_WIDTH: Final = 2
_MAX_AGENT_LOG_DIRECTORIES: Final = 16
_AGENT_FILES: Final = ("context.jsonl", "api.jsonl", "bash.jsonl", "events.jsonl")
_ARCHIVED_LEGACY_PREFIX: Final = "legacy-"
_MAX_MANIFEST_BYTES: Final = 16 * 1024


@dataclass(frozen=True, slots=True)
class _Epoch:
    index: int
    count: int
    start: datetime | None
    end: datetime | None


class TranscriptReader:
    def __init__(self, match_dir: Path, filters: TranscriptFilters) -> None:
        self._path = match_dir
        self._filters = filters
        self._budget = FrameBudget()

    def read(self) -> TranscriptDocument:
        if not self._path.exists():
            raise TranscriptError(f"match directory does not exist: {self._path}")
        if self._path.is_symlink() or not self._path.is_dir():
            raise TranscriptError(f"match path is not a directory: {self._path}")
        agents_dir = self._path / "agents"
        if agents_dir.is_symlink() or not agents_dir.is_dir():
            raise TranscriptError(f"agents directory not found: {agents_dir}")
        manifest = self._read_manifest()
        epoch = self._select_epoch(manifest)
        slots = self._agent_slots(agents_dir)
        if self._filters.agent is not None:
            if self._filters.agent not in slots:
                raise TranscriptError(f"agent slot {self._filters.agent} not found")
            slots = (self._filters.agent,)
        reservations = self._reservations(epoch)
        agents = tuple(self._read_agent(slot, epoch, reservations) for slot in slots)
        if self._filters.turn is not None and not any(agent.turns for agent in agents):
            raise TranscriptError(f"turn {self._filters.turn} not found")
        if manifest is None:
            match_id = (
                self._path.parent.name
                if self._path.parent.parent.name == "archive"
                and self._path.name.startswith(_ARCHIVED_LEGACY_PREFIX)
                else self._path.name
            )
            run = RunInfo(
                match_id=match_id,
                legacy=True,
                legacy_run=epoch.index,
                legacy_run_count=epoch.count,
                started_at=epoch.start,
            )
        else:
            run = RunInfo(
                match_id=str(manifest.match_id),
                run_id=manifest.run_id,
                started_at=manifest.started_at,
                legacy=False,
            )
        return TranscriptDocument(run=run, agents=agents)

    def _read_manifest(self) -> RunManifest | None:
        path = self._path / "run.json"
        if not path.exists():
            return None
        if path.is_symlink() or not path.is_file():
            raise TranscriptError(f"run manifest is not a file: {path}")
        try:
            with path.open("rb") as handle:
                raw = handle.readline(_MAX_MANIFEST_BYTES + 1)
            if len(raw) > _MAX_MANIFEST_BYTES:
                raise TranscriptError(f"oversized run manifest: {path}")
            return RunManifest.model_validate_json(raw)
        except (OSError, UnicodeError, ValidationError) as exc:
            raise TranscriptError(f"invalid run manifest: {path}") from exc

    def _select_epoch(self, manifest: RunManifest | None) -> _Epoch:
        if manifest is not None:
            if self._filters.legacy_run is not None:
                raise TranscriptError("--legacy-run cannot be used with an isolated run")
            return _Epoch(index=0, count=1, start=None, end=None)
        starts: list[datetime] = []
        for envelope in read_jsonl(self._path / "match.jsonl", self._budget):
            if envelope.kind != "match_state_change":
                continue
            try:
                state = StateChangeFrame.model_validate(envelope.data)
            except ValidationError as exc:
                raise TranscriptError("invalid match_state_change") from exc
            if state.from_state == "IDLE" and state.to_state == "VM_BOOTING":
                starts.append(envelope.ts)
        if not starts:
            starts.append(datetime.min.replace(tzinfo=UTC))
        index = (
            self._filters.legacy_run if self._filters.legacy_run is not None else len(starts) - 1
        )
        if index >= len(starts):
            available = f"0..{len(starts) - 1}"
            raise TranscriptError(f"legacy run {index} not found; available range is {available}")
        end = starts[index + 1] if index + 1 < len(starts) else None
        return _Epoch(index=index, count=len(starts), start=starts[index], end=end)

    def _agent_slots(self, agents_dir: Path) -> tuple[int, ...]:
        slots = tuple(
            sorted(
                int(entry.name)
                for entry in agents_dir.iterdir()
                if not entry.is_symlink()
                and entry.is_dir()
                and len(entry.name) == _AGENT_NAME_WIDTH
                and entry.name.isdigit()
            )
        )
        if not slots:
            raise TranscriptError(f"no agent log directories found under {agents_dir}")
        if len(slots) > _MAX_AGENT_LOG_DIRECTORIES:
            raise TranscriptError(f"agent log directory count exceeds {_MAX_AGENT_LOG_DIRECTORIES}")
        return slots

    def _reservations(self, epoch: _Epoch) -> dict[tuple[int, str, int], Reservation]:
        reservations: dict[tuple[int, str, int], Reservation] = {}
        for envelope in epoch_lines(
            self._path / "match.jsonl", epoch.start, epoch.end, self._budget
        ):
            if envelope.kind != "llm_reservation_decision":
                continue
            try:
                frame = ReservationFrame.model_validate(envelope.data)
            except ValidationError as exc:
                raise TranscriptError("invalid llm_reservation_decision") from exc
            destination = envelope.dst or ""
            if not destination.startswith("agent") or not destination[5:].isdigit():
                continue
            slot = int(destination[5:])
            if slot >= _MAX_AGENT_LOG_DIRECTORIES:
                continue
            reservations[(slot, frame.request_id, frame.attempt)] = Reservation(
                granted=frame.granted,
                reason=frame.reason,
                reserved_nano_usd=frame.reserved_nano_usd,
            )
        return reservations

    def _read_agent(
        self,
        slot: int,
        epoch: _Epoch,
        reservations: dict[tuple[int, str, int], Reservation],
    ) -> Agent:
        directory = self._path / "agents" / f"{slot:02d}"
        envelopes = merge(
            *(
                epoch_lines(directory / name, epoch.start, epoch.end, self._budget)
                for name in _AGENT_FILES
            ),
            key=attrgetter("ts"),
        )
        requests: dict[AttemptKey, RequestFrame] = {}
        contexts: dict[AttemptKey, ContextFrame] = {}
        context_chunks: dict[AttemptKey, list[ContextChunkFrame]] = {}
        failures: dict[AttemptKey, FailureFrame] = {}
        responses: dict[AttemptKey, ResponseFrame] = {}
        bash_requests: list[BashRequestFrame] = []
        bash_results: dict[str, BashResultFrame] = {}
        summaries: dict[int, TurnSummaryFrame] = {}
        harness_exit: HarnessExitFrame | None = None
        for envelope in envelopes:
            try:
                match envelope.kind:
                    case "llm_request":
                        frame = RequestFrame.model_validate(envelope.data)
                        requests[AttemptKey(frame.turn, frame.request_id, frame.attempt)] = frame
                    case "llm_context_snapshot":
                        frame = ContextFrame.model_validate(envelope.data)
                        contexts[AttemptKey(frame.turn, frame.request_id, frame.attempt)] = frame
                    case "llm_context_chunk":
                        frame = ContextChunkFrame.model_validate(envelope.data)
                        key = AttemptKey(frame.turn, frame.request_id, frame.attempt)
                        context_chunks.setdefault(key, []).append(frame)
                    case "llm_attempt_failure":
                        frame = FailureFrame.model_validate(envelope.data)
                        failures[AttemptKey(frame.turn, frame.request_id, frame.attempt)] = frame
                    case "llm_response":
                        frame = ResponseFrame.model_validate(envelope.data)
                        responses[AttemptKey(frame.turn, frame.request_id, frame.attempt)] = frame
                    case "bash_request":
                        bash_requests.append(BashRequestFrame.model_validate(envelope.data))
                    case "bash_result":
                        frame = BashResultFrame.model_validate(envelope.data)
                        bash_results[frame.request_id] = frame
                    case "turn_summary":
                        frame = TurnSummaryFrame.model_validate(envelope.data)
                        summaries[frame.turn] = frame
                    case "harness_exit":
                        harness_exit = HarnessExitFrame.model_validate(envelope.data)
                    case _:
                        continue
            except ValidationError as exc:
                raise TranscriptError(f"invalid {envelope.kind} for agent {slot}") from exc
        turns = build_turns(
            AssemblyInput(
                requests=requests,
                contexts=contexts,
                context_chunks=context_chunks,
                failures=failures,
                responses=responses,
                bash_requests=bash_requests,
                bash_results=bash_results,
                summaries=summaries,
                reservations=reservations,
                slot=slot,
                legacy=epoch.start is not None,
                turn_filter=self._filters.turn,
            )
        )
        exit_model = (
            None
            if harness_exit is None
            else HarnessExit(
                reason=harness_exit.reason,
                code=harness_exit.code,
                last_turn=harness_exit.last_turn,
            )
        )
        return Agent(slot=slot, turns=turns, harness_exit=exit_model)
