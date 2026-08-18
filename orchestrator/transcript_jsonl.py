from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import JsonValue, TypeAdapter, ValidationError

from common.protocol import MAX_FRAME_BYTES, parse_envelope
from orchestrator.transcript import TranscriptError
from orchestrator.transcript_frames import Envelope

MAX_TRANSCRIPT_FRAMES: Final = 10_000
_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])


class FrameBudget:
    __slots__ = ("remaining",)

    def __init__(self) -> None:
        self.remaining = MAX_TRANSCRIPT_FRAMES

    def consume(self, path: Path, line_number: int) -> None:
        if self.remaining == 0:
            raise TranscriptError(
                f"transcript frame count exceeds {MAX_TRANSCRIPT_FRAMES} at {path}:{line_number}"
            )
        self.remaining -= 1


def _validation_summary(exc: ValidationError) -> str:
    details = exc.errors(include_url=False, include_context=False, include_input=False)
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['type']}"
        for error in details
    )


def read_jsonl(path: Path, budget: FrameBudget | None = None) -> Iterator[Envelope]:
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise TranscriptError(f"log path is not a file: {path}")
    active_budget = budget or FrameBudget()
    try:
        with path.open("rb") as handle:
            line_number = 0
            while raw := handle.readline(MAX_FRAME_BYTES + 1):
                line_number += 1
                frame = raw[:-1] if raw.endswith(b"\n") else raw
                if len(frame) > MAX_FRAME_BYTES:
                    raise TranscriptError(f"oversized JSONL line at {path}:{line_number}")
                if not frame.strip():
                    continue
                active_budget.consume(path, line_number)
                try:
                    text = frame.decode("utf-8")
                    yield parse_envelope(_normalize_legacy_frame(text))
                except UnicodeError as exc:
                    raise TranscriptError(
                        f"malformed JSONL at {path}:{line_number}: invalid_utf8"
                    ) from exc
                except ValidationError as exc:
                    summary = _validation_summary(exc)
                    raise TranscriptError(
                        f"malformed JSONL at {path}:{line_number}: {summary}"
                    ) from exc
                except ValueError as exc:
                    raise TranscriptError(
                        f"malformed JSONL at {path}:{line_number}: invalid_json"
                    ) from exc
    except OSError as exc:
        raise TranscriptError(f"cannot read log {path}: {type(exc).__name__}") from exc


def _normalize_legacy_frame(text: str) -> str:
    try:
        raw = _JSON_OBJECT.validate_json(text)
    except ValidationError:
        return text
    kind = raw.get("kind")
    data = raw.get("data")
    if not isinstance(data, dict):
        return text
    if kind == "pid_announce" and "budget_protocol_version" in data:
        normalized_data = dict(data)
        normalized_data["budget_capability_version"] = normalized_data.pop(
            "budget_protocol_version"
        )
        return json.dumps({**raw, "data": normalized_data}, separators=(",", ":"))
    if kind != "llm_reservation_decision":
        return text
    turn = data.get("turn")
    reason = data.get("reason")
    if turn is None and reason is not None:
        return text
    normalized_data = dict(data)
    normalized_data.pop("turn", None)
    if reason is None:
        normalized_data["reason"] = "granted" if data.get("granted") is True else "denied"
    normalized = {**raw, "data": normalized_data}
    return json.dumps(normalized, separators=(",", ":"), ensure_ascii=False)


def epoch_lines(
    path: Path,
    start: datetime | None,
    end: datetime | None,
    budget: FrameBudget | None = None,
) -> Iterator[Envelope]:
    for envelope in read_jsonl(path, budget):
        if start is not None and envelope.ts < start:
            continue
        if end is not None and envelope.ts >= end:
            continue
        yield envelope
