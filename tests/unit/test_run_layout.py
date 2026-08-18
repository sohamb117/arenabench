from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Final, cast

import pytest
from typer.testing import CliRunner

from common.ids import MatchId, make_match_id
from orchestrator import logger as logger_module
from orchestrator.cli import app
from orchestrator.logger import MatchLogger

_MATCH_ID: Final = make_match_id("repeatable-match")
_N_AGENTS: Final = 2
_PRIVATE_DIR_MODE: Final = 0o700
_PRIVATE_FILE_MODE: Final = 0o600
_RUN_ID_PATTERN: Final = re.compile(r"^\d{8}T\d{15}Z-[0-9a-f]{12}$")


def _manifest(match_dir: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads((match_dir / "run.json").read_text(encoding="utf-8")))


def test_first_run_creates_manifest_and_private_empty_files(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"

    with MatchLogger(log_root, _MATCH_ID, _N_AGENTS) as logger:
        match_dir = logger.match_dir

    manifest = _manifest(match_dir)
    assert manifest.keys() == {"schema_version", "match_id", "run_id", "started_at", "n_agents"}
    assert manifest["schema_version"] == 1
    assert manifest["match_id"] == str(_MATCH_ID)
    assert manifest["n_agents"] == _N_AGENTS
    assert _RUN_ID_PATTERN.fullmatch(cast(str, manifest["run_id"]))
    assert (match_dir / "match.jsonl").read_text(encoding="utf-8") == ""
    assert stat.S_IMODE(match_dir.stat().st_mode) == _PRIVATE_DIR_MODE
    assert stat.S_IMODE((match_dir / "run.json").stat().st_mode) == _PRIVATE_FILE_MODE
    assert stat.S_IMODE((match_dir / "match.jsonl").stat().st_mode) == _PRIVATE_FILE_MODE


def test_second_run_archives_first_intact_and_starts_fresh(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"
    with MatchLogger(log_root, _MATCH_ID, _N_AGENTS) as first_logger:
        first_dir = first_logger.match_dir
    first_manifest = _manifest(first_dir)
    first_run_id = cast(str, first_manifest["run_id"])
    (first_dir / "match.jsonl").write_text("first execution\n", encoding="utf-8")

    with MatchLogger(log_root, _MATCH_ID, _N_AGENTS) as second_logger:
        second_dir = second_logger.match_dir

    second_manifest = _manifest(second_dir)
    second_run_id = cast(str, second_manifest["run_id"])
    archived_dir = log_root / "archive" / str(_MATCH_ID) / first_run_id
    assert (archived_dir / "match.jsonl").read_text(encoding="utf-8") == "first execution\n"
    assert _manifest(archived_dir) == first_manifest
    assert (second_dir / "match.jsonl").read_text(encoding="utf-8") == ""
    assert first_run_id != second_run_id
    assert [first_run_id, second_run_id] == sorted((first_run_id, second_run_id))


def test_archive_collision_fails_closed_without_moving_current_run(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"
    with MatchLogger(log_root, _MATCH_ID, _N_AGENTS) as logger:
        current_dir = logger.match_dir
    manifest = _manifest(current_dir)
    run_id = cast(str, manifest["run_id"])
    (current_dir / "match.jsonl").write_text("must remain current\n", encoding="utf-8")
    collision = log_root / "archive" / str(_MATCH_ID) / run_id
    collision.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        MatchLogger(log_root, _MATCH_ID, _N_AGENTS)

    assert (current_dir / "match.jsonl").read_text(encoding="utf-8") == "must remain current\n"
    assert collision.is_dir()


def test_match_logger_exclusively_opens_run_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_dir = tmp_path / "prepared-run"
    run_dir.mkdir()
    (run_dir / "match.jsonl").write_text("collision\n", encoding="utf-8")

    def prepared_run_directory(_log_root: Path, _match_id: MatchId, _n_agents: int) -> Path:
        return run_dir

    monkeypatch.setattr(logger_module, "prepare_run_directory", prepared_run_directory)

    with pytest.raises(FileExistsError):
        MatchLogger(tmp_path / "logs", _MATCH_ID, _N_AGENTS)


def test_replay_accepts_latest_run_direct_path(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"
    with MatchLogger(log_root, _MATCH_ID, _N_AGENTS):
        pass
    with MatchLogger(log_root, _MATCH_ID, _N_AGENTS) as logger:
        logger.write_summary(
            {
                "result": "victory",
                "winner": 0,
                "cause": "opponent_crashed",
                "final_state": "DONE",
                "estimated_spend_usd": 0.0,
                "estimated_spend_by_agent_usd": {"0": 0.0, "1": 0.0},
                "budget_usd": None,
                "per_agent_budget_usd": None,
            }
        )
        current_dir = logger.match_dir

    result = CliRunner().invoke(app, ["replay", str(current_dir)])

    assert result.exit_code == 0
    assert cast(dict[str, object], json.loads(result.stdout))["winner"] == 0
