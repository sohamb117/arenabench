from __future__ import annotations

import errno
import json
import os
import re
import sys
import time
import uuid
from ctypes import CDLL, c_char_p, c_int, c_uint, get_errno
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, Literal, NewType

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from common.ids import MatchId, make_match_id

RunId = NewType("RunId", str)

_PRIVATE_DIR_MODE: Final = 0o700
_PRIVATE_FILE_MODE: Final = 0o600
_RUN_ID_PATTERN: Final = r"^\d{8}T\d{15}Z-[0-9a-f]{12}$"
_SCHEMA_VERSION: Final = 1
_AT_FDCWD: Final = -2
_RENAME_NOREPLACE: Final = 1
_RENAME_EXCL: Final = 4


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    match_id: MatchId
    run_id: Annotated[str, Field(pattern=_RUN_ID_PATTERN)]
    started_at: datetime
    n_agents: Annotated[int, Field(ge=1, le=16)]

    @field_validator("match_id")
    @classmethod
    def _validate_match_id(cls, value: str) -> MatchId:
        return make_match_id(value)

    @field_validator("run_id")
    @classmethod
    def _validate_run_id(cls, value: str) -> RunId:
        return make_run_id(value)


class InvalidRunIdError(ValueError):
    pass


def make_run_id(value: str) -> RunId:
    if re.fullmatch(_RUN_ID_PATTERN, value) is None:
        raise InvalidRunIdError
    return RunId(value)


@dataclass(frozen=True, slots=True)
class ArchiveMoveError(Exception):
    source: Path
    destination: Path

    def __str__(self) -> str:
        return f"cannot archive {self.source} across filesystems to {self.destination}"


def prepare_run_directory(log_root: Path, match_id: MatchId, n_agents: int) -> Path:
    """Archive any prior run and create a private directory for a new run."""
    manifest = _new_manifest(match_id, n_agents)
    matches_dir = log_root / "matches"
    current_dir = matches_dir / str(match_id)
    _ensure_log_root(log_root)
    _mkdir_private(matches_dir)
    if current_dir.is_symlink() or current_dir.exists():
        _archive_current_run(log_root, current_dir, match_id)
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
) -> None:
    if current_dir.is_symlink() or not current_dir.is_dir():
        raise NotADirectoryError(os.fspath(current_dir))
    manifest_path = current_dir / "run.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        try:
            manifest = RunManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError):
            archive_name = f"corrupt-{_new_sortable_id()}"
        else:
            archive_name = manifest.run_id
    else:
        archive_name = f"legacy-{_new_sortable_id()}"
    archive_dir = log_root / "archive"
    _mkdir_private(archive_dir)
    archive_match_dir = archive_dir / str(match_id)
    _mkdir_private(archive_match_dir)
    destination = archive_match_dir / archive_name
    try:
        _rename_no_replace(current_dir, destination)
        destination.chmod(_PRIVATE_DIR_MODE)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            raise ArchiveMoveError(current_dir, destination) from exc
        raise


def _write_manifest(run_dir: Path, manifest: RunManifest) -> None:
    manifest_path = run_dir / "run.json"
    rendered = json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True)
    with manifest_path.open("x", encoding="utf-8") as handle:
        handle.write(f"{rendered}\n")
    manifest_path.chmod(_PRIVATE_FILE_MODE)


def _mkdir_private(path: Path) -> None:
    if path.is_symlink():
        raise NotADirectoryError(os.fspath(path))
    path.mkdir(parents=True, exist_ok=True, mode=_PRIVATE_DIR_MODE)
    if not path.is_dir():
        raise NotADirectoryError(os.fspath(path))
    path.chmod(_PRIVATE_DIR_MODE)


def _ensure_log_root(log_root: Path) -> None:
    if log_root.is_symlink():
        raise NotADirectoryError(os.fspath(log_root))
    if log_root.exists():
        if not log_root.is_dir():
            raise NotADirectoryError(os.fspath(log_root))
        return
    log_root.mkdir(parents=True, mode=_PRIVATE_DIR_MODE)


def _new_sortable_id() -> str:
    started_ns = time.time_ns()
    timestamp = datetime.fromtimestamp(started_ns // 1_000_000_000, tz=UTC).strftime(
        "%Y%m%dT%H%M%S"
    )
    return f"{timestamp}{started_ns % 1_000_000_000:09d}Z-{uuid.uuid4().hex[:12]}"


def _rename_no_replace(source: Path, destination: Path) -> None:
    libc = CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    match sys.platform:
        case "linux":
            try:
                rename = libc.renameat2
            except AttributeError as exc:
                raise OSError(errno.ENOTSUP, "renameat2 is unavailable") from exc
            rename.argtypes = (c_int, c_char_p, c_int, c_char_p, c_uint)
            rename.restype = c_int
            if (
                rename(
                    _AT_FDCWD,
                    source_bytes,
                    _AT_FDCWD,
                    destination_bytes,
                    _RENAME_NOREPLACE,
                )
                == 0
            ):
                return
        case "darwin":
            try:
                rename = libc.renamex_np
            except AttributeError as exc:
                raise OSError(errno.ENOTSUP, "renamex_np is unavailable") from exc
            rename.argtypes = (c_char_p, c_char_p, c_uint)
            rename.restype = c_int
            if rename(source_bytes, destination_bytes, _RENAME_EXCL) == 0:
                return
        case _:
            raise OSError(errno.ENOTSUP, "exclusive directory rename is unsupported")
    error_number = get_errno()
    raise OSError(error_number, os.strerror(error_number), os.fspath(destination))
