from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from orchestrator.transcript import TranscriptError
from orchestrator.transcript_frames import Envelope

_MAX_LINE_BYTES: Final = 1_048_576


def read_jsonl(path: Path) -> Iterator[Envelope]:
    if not path.exists():
        return
    if path.is_symlink() or not path.is_file():
        raise TranscriptError(f"log path is not a file: {path}")
    try:
        with path.open("rb") as handle:
            for line_number, raw in enumerate(handle, start=1):
                if len(raw) > _MAX_LINE_BYTES:
                    raise TranscriptError(f"oversized JSONL line at {path}:{line_number}")
                if not raw.strip():
                    continue
                try:
                    yield Envelope.model_validate_json(raw)
                except ValidationError as exc:
                    location = f"{path}:{line_number}"
                    raise TranscriptError(f"malformed JSONL at {location}: {exc}") from exc
    except OSError as exc:
        raise TranscriptError(f"cannot read log {path}: {exc}") from exc


def epoch_lines(path: Path, start: datetime | None, end: datetime | None) -> Iterator[Envelope]:
    for envelope in read_jsonl(path):
        if start is not None and envelope.ts < start:
            continue
        if end is not None and envelope.ts >= end:
            continue
        yield envelope
