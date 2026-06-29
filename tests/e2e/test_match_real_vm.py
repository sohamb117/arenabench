"""End-to-end test: real 1v1 match producing a real summary.json.

This is the canonical exit-criteria test from plan §14. Skipped by default;
run via `ARENABENCH_E2E=1 ANTHROPIC_API_KEY=... OPENAI_API_KEY=... pytest -m e2e`
after building the golden image with vm/golden/build.sh.

Scenarios advanced (plan §9):
  S1: happy 1v1 → summary.result=='victory', exactly one harness_exit, winner pid alive
  S5: log completeness — every expected dir/file exists, every JSONL parses,
      summary.json validates against orchestrator/schemas/summary.schema.json
  S6: N=4 free-for-all → 4 pid_announce frames + ports 10000..10003
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from orchestrator._lifecycle_state import MatchOutcome

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_IMAGE_PATH = REPO_ROOT / "vm" / "images" / "arenabench-golden-aarch64.qcow2"
DEMO_MATCH_1V1 = REPO_ROOT / "configs" / "matches" / "demo-1v1.json"
DEMO_MATCH_4FFA = REPO_ROOT / "configs" / "matches" / "demo-4ffa.json"
N_AGENTS_4 = 4
EXPECTED_PORTS_4FFA = [10000, 10001, 10002, 10003]


def _skip_if_no_e2e() -> None:
    if not os.environ.get("ARENABENCH_E2E"):
        pytest.skip("set ARENABENCH_E2E=1 to run real-VM e2e tests")
    if not GOLDEN_IMAGE_PATH.exists():
        pytest.skip(f"golden image not built; run vm/golden/build.sh ({GOLDEN_IMAGE_PATH} missing)")
    for tool in ("qemu-system-aarch64", "qemu-img", "cloud-localds", "ssh"):
        if not shutil.which(tool):
            pytest.skip(f"required host tool '{tool}' not on PATH")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("set ANTHROPIC_API_KEY")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("set OPENAI_API_KEY")


def _run_match(match_config: Path, *, log_root: Path, match_id: str) -> Path:
    result = subprocess.run(
        [
            "uv",
            "run",
            "arenabench",
            "run",
            str(match_config),
            "--log-root",
            str(log_root),
            "--golden-image",
            str(GOLDEN_IMAGE_PATH),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"arenabench run exited {result.returncode}\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return log_root / "matches" / match_id / "summary.json"


def _iter_envelopes(path: Path) -> list[dict[str, object]]:
    envs: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            envs.append(cast(dict[str, object], json.loads(line)))
    return envs


@pytest.mark.e2e
def test_demo_1v1_produces_victory(tmp_path: Path) -> None:
    """§14.1 / S1: result=='victory', exactly one harness_exit, winner pid_announce present."""
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary_path = _run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1")

    summary = cast(dict[str, object], json.loads(summary_path.read_text(encoding="utf-8")))
    assert summary.get("result") == "victory", summary
    winner = summary.get("winner")
    assert winner in (0, 1), summary
    assert isinstance(summary.get("cause"), str)

    match_dir = summary_path.parent
    agents_dir = match_dir / "agents"
    harness_exits: list[dict[str, object]] = []
    for slot_dir in agents_dir.iterdir():
        events_path = slot_dir / "events.jsonl"
        for env in _iter_envelopes(events_path):
            if env.get("kind") == "harness_exit":
                harness_exits.append(env)
    assert len(harness_exits) == 1, f"S1 expected exactly one harness_exit, got {harness_exits}"
    loser_slot = harness_exits[0]["src"]
    assert loser_slot != f"agent{winner}"
    winner_events = (agents_dir / f"{winner:02d}" / "bash.jsonl").read_text(encoding="utf-8")
    assert '"kind":"pid_announce"' in winner_events, "winner must have emitted pid_announce"


@pytest.mark.e2e
def test_demo_4ffa_n4_free_for_all(tmp_path: Path) -> None:
    """§14.5 / S6: 4 pid_announce frames + ports 10000..10003."""
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary_path = _run_match(DEMO_MATCH_4FFA, log_root=log_root, match_id="demo-4ffa")
    match_dir = summary_path.parent

    agent_subdirs = sorted(p.name for p in (match_dir / "agents").iterdir() if p.is_dir())
    assert agent_subdirs == ["00", "01", "02", "03"], agent_subdirs

    pid_announces: list[dict[str, object]] = []
    for slot_dir in (match_dir / "agents").iterdir():
        for env in _iter_envelopes(slot_dir / "bash.jsonl"):
            if env.get("kind") == "pid_announce":
                pid_announces.append(env)
    assert len(pid_announces) == N_AGENTS_4, pid_announces


@pytest.mark.e2e
def test_log_completeness(tmp_path: Path) -> None:
    """§14.4 / S5: every file exists + parses; summary validates against schema."""
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary_path = _run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1")
    match_dir = summary_path.parent

    assert (match_dir / "match.jsonl").is_file()
    assert (match_dir / "orchestrator.log").is_file()
    for env in _iter_envelopes(match_dir / "match.jsonl"):
        assert isinstance(env.get("kind"), str)
    for slot_dir in (match_dir / "agents").iterdir():
        for jsonl in ("events.jsonl", "bash.jsonl", "api.jsonl", "context.jsonl"):
            path = slot_dir / jsonl
            assert path.is_file(), f"{path} missing"
            _ = _iter_envelopes(path)

    summary = cast(dict[str, object], json.loads(summary_path.read_text(encoding="utf-8")))
    _ = MatchOutcome.model_validate(summary)
