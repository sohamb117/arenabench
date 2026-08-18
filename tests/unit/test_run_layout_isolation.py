from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Final, cast

import pytest

from common.ids import make_match_id
from orchestrator.logger import MatchLogger
from orchestrator.run_layout import ArchiveMoveError, prepare_run_directory
from orchestrator.transcript import read_transcript
from tests.unit.transcript_fixtures import legacy_run

_N_AGENTS: Final = 2
_PRIVATE_DIR_MODE: Final = 0o700
_PRIVATE_FILE_MODE: Final = 0o600
_CALLER_ROOT_MODE: Final = 0o755
_LEGACY_EPOCH_COUNT: Final = 2


def _files(directory: Path) -> dict[Path, bytes]:
    return {
        path.relative_to(directory): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


def _only_archive(log_root: Path, match_id: str, prefix: str) -> Path:
    archives = tuple((log_root / "archive" / match_id).glob(f"{prefix}-*"))
    assert len(archives) == 1
    return archives[0]


def test_legacy_archive_preserves_bytes_and_epoch_selection(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"
    current = legacy_run(log_root / "matches")
    before = _files(current)

    with MatchLogger(log_root, make_match_id("legacy"), _N_AGENTS):
        pass

    archived = _only_archive(log_root, "legacy", "legacy")
    latest = read_transcript(archived)
    first = read_transcript(archived, legacy_run=0)
    assert _files(archived) == before
    assert not (archived / "run.json").exists()
    assert latest.run.match_id == "legacy"
    assert latest.run.legacy is True
    assert latest.run.legacy_run == 1
    assert latest.run.legacy_run_count == _LEGACY_EPOCH_COUNT
    assert latest.agents[0].turns[0].attempts[0].request.request_id == "new"
    latest_attempt = latest.agents[0].turns[0].attempts[0]
    assert latest_attempt.context.kind == "unavailable"
    assert latest_attempt.context.reason == "legacy_log"
    assert latest_attempt.outcome.kind == "unavailable"
    assert latest_attempt.outcome.reason == "legacy_log"
    assert first.run.legacy_run == 0
    assert first.agents[0].turns[0].attempts[0].request.request_id == "old"


def test_corrupt_manifest_is_quarantined_before_fresh_run(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"
    current = log_root / "matches" / "corrupt"
    current.mkdir(parents=True)
    (current / "run.json").write_bytes(b'{"schema_version": 1')
    (current / "evidence.bin").write_bytes(b"\x00do-not-change\xff")
    before = _files(current)

    with MatchLogger(log_root, make_match_id("corrupt"), _N_AGENTS) as logger:
        fresh = logger.match_dir

    archived = _only_archive(log_root, "corrupt", "corrupt")
    assert _files(archived) == before
    assert json.loads((fresh / "run.json").read_text(encoding="utf-8"))["match_id"] == "corrupt"
    assert fresh != archived


def test_symlink_log_root_is_rejected_without_mutating_target(tmp_path: Path) -> None:
    target = tmp_path / "caller-owned"
    target.mkdir(mode=_CALLER_ROOT_MODE)
    log_root = tmp_path / "logs"
    log_root.symlink_to(target, target_is_directory=True)

    with pytest.raises((NotADirectoryError, OSError)):
        MatchLogger(log_root, make_match_id("symlink-root"), _N_AGENTS)

    assert stat.S_IMODE(target.stat().st_mode) == _CALLER_ROOT_MODE
    assert tuple(target.iterdir()) == ()


def test_existing_log_root_mode_is_unchanged(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"
    log_root.mkdir(mode=_CALLER_ROOT_MODE)
    original_mode = stat.S_IMODE(log_root.stat().st_mode)

    with MatchLogger(log_root, make_match_id("owned-root"), _N_AGENTS):
        pass

    assert stat.S_IMODE(log_root.stat().st_mode) == original_mode


@pytest.mark.parametrize("owned_name", ["matches", "archive"])
def test_symlinked_owned_directory_is_rejected(tmp_path: Path, owned_name: str) -> None:
    log_root = tmp_path / "logs"
    target = tmp_path / "attacker-owned"
    log_root.mkdir()
    target.mkdir()
    if owned_name == "archive":
        current = log_root / "matches" / "owned-symlink"
        current.mkdir(parents=True)
    (log_root / owned_name).symlink_to(target, target_is_directory=True)

    with pytest.raises(NotADirectoryError):
        MatchLogger(log_root, make_match_id("owned-symlink"), _N_AGENTS)

    assert tuple(target.iterdir()) == ()


def test_cross_filesystem_archive_error_preserves_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_root = tmp_path / "logs"
    match_id = make_match_id("cross-device")
    with MatchLogger(log_root, match_id, _N_AGENTS) as logger:
        current = logger.match_dir
    before = _files(current)

    def cross_device(_source: Path, _destination: Path) -> None:
        raise OSError(18, "Cross-device link")

    monkeypatch.setattr("orchestrator.run_layout._rename_no_replace", cross_device)

    with pytest.raises(ArchiveMoveError):
        prepare_run_directory(log_root, match_id, _N_AGENTS)

    assert _files(current) == before


def test_owned_log_children_are_private(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"
    match_id = make_match_id("private-children")
    with MatchLogger(log_root, match_id, _N_AGENTS):
        pass
    with MatchLogger(log_root, match_id, _N_AGENTS) as logger:
        current = logger.match_dir

    archived_run_id = cast(
        str,
        json.loads(
            next((log_root / "archive" / str(match_id)).iterdir())
            .joinpath("run.json")
            .read_text(encoding="utf-8")
        )["run_id"],
    )
    owned_directories = (
        log_root / "matches",
        log_root / "archive",
        log_root / "archive" / str(match_id),
        log_root / "archive" / str(match_id) / archived_run_id,
        current,
        current / "agents",
        current / "agents" / "00",
    )
    for directory in owned_directories:
        assert stat.S_IMODE(directory.stat().st_mode) == _PRIVATE_DIR_MODE
    for path in current.rglob("*"):
        if path.is_file():
            assert stat.S_IMODE(path.stat().st_mode) == _PRIVATE_FILE_MODE
