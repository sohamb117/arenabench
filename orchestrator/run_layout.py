from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field

from common.ids import MatchId

RunId = NewType("RunId", str)

_PRIVATE_DIR_MODE: Final = 0o700
_PRIVATE_FILE_MODE: Final = 0o600
_RUN_ID_PATTERN: Final = r"^\d{8}T\d{15}Z-[0-9a-f]{12}$"
_SCHEMA_VERSION: Final = 1


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    match_id: MatchId
    run_id: Annotated[str, Field(pattern=_RUN_ID_PATTERN)]
    started_at: datetime
    n_agents: Annotated[int, Field(ge=1, le=16)]


def prepare_run_directory(log_root: Path, match_id: MatchId, n_agents: int) -> Path:
    """Archive any prior run and create a private directory for a new run."""
    manifest = _new_manifest(match_id, n_agents)
    matches_dir = log_root / "matches"
    current_dir = matches_dir / str(match_id)
    _mkdir_private(log_root)
    _mkdir_private(matches_dir)
    if current_dir.exists():
        _archive_current_run(log_root, current_dir, match_id, n_agents)
    current_dir.mkdir(mode=_PRIVATE_DIR_MODE)
    current_dir.chmod(_PRIVATE_DIR_MODE)
    _write_manifest(current_dir, manifest)
    return current_dir


def _new_manifest(match_id: MatchId, n_agents: int) -> RunManifest:
    started_ns = time.time_ns()
    started_at = datetime.fromtimestamp(started_ns / 1_000_000_000, tz=UTC)
    timestamp = datetime.fromtimestamp(started_ns // 1_000_000_000, tz=UTC).strftime(
        "%Y%m%dT%H%M%S"
    )
    nanoseconds = started_ns % 1_000_000_000
    run_id = RunId(f"{timestamp}{nanoseconds:09d}Z-{uuid.uuid4().hex[:12]}")
    return RunManifest(
        match_id=match_id,
        run_id=run_id,
        started_at=started_at,
        n_agents=n_agents,
    )


def _archive_current_run(
    log_root: Path,
    current_dir: Path,
    match_id: MatchId,
    n_agents: int,
) -> None:
    manifest_path = current_dir / "run.json"
    if manifest_path.is_file():
        manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = _new_manifest(match_id, n_agents)
        _write_manifest(current_dir, manifest)
    archive_match_dir = log_root / "archive" / str(match_id)
    _mkdir_private(archive_match_dir)
    archive_dir = archive_match_dir / manifest.run_id
    if archive_dir.exists():
        raise FileExistsError(os.fspath(archive_dir))
    current_dir.rename(archive_dir)


def _write_manifest(run_dir: Path, manifest: RunManifest) -> None:
    manifest_path = run_dir / "run.json"
    rendered = json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True)
    with manifest_path.open("x", encoding="utf-8") as handle:
        handle.write(f"{rendered}\n")
    manifest_path.chmod(_PRIVATE_FILE_MODE)


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
    path.chmod(_PRIVATE_DIR_MODE)
