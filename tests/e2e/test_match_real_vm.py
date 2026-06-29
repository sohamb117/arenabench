"""End-to-end test: real 1v1 match producing a real summary.json.

This is the canonical exit-criteria test from plan §14. Skipped by default;
run via `ARENABENCH_E2E=1 ANTHROPIC_API_KEY=... OPENAI_API_KEY=... pytest -m e2e`
after building the golden image with vm/golden/build.sh.

The test wires together: orchestrator.match_config.load_match_config,
orchestrator.vm.QemuVm, orchestrator.vsock_server.VsockServer (or harness
SshTransport via R2 fallback), orchestrator.logger.MatchLogger, and
orchestrator.lifecycle.run_match.

Scenarios advanced (plan §9):
  S1: happy 1v1 → summary.json.result=="victory"
  S5: log completeness — every expected dir + file exists, every JSONL parses
  S6: N=4 free-for-all is exercised by test_match_real_vm_n4_free_for_all
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_IMAGE_PATH = REPO_ROOT / "vm" / "images" / "arenabench-golden-aarch64.qcow2"
DEMO_MATCH_1V1 = REPO_ROOT / "configs" / "matches" / "demo-1v1.json"
DEMO_MATCH_4FFA = REPO_ROOT / "configs" / "matches" / "demo-4ffa.json"


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


@pytest.mark.e2e
def test_demo_1v1_produces_victory(tmp_path: Path) -> None:
    """Exit-criterion §14.1: real S1 1v1 produces summary.json.result=='victory'."""
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()

    summary_path = _run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1")

    assert summary_path.exists(), f"summary.json missing at {summary_path}"
    summary_raw = summary_path.read_text(encoding="utf-8")
    summary = cast(dict[str, object], json.loads(summary_raw))
    assert summary.get("result") == "victory", f"expected victory, got: {summary}"
    assert summary.get("winner") in (0, 1)
    assert isinstance(summary.get("cause"), str)


@pytest.mark.e2e
def test_demo_4ffa_n4_free_for_all(tmp_path: Path) -> None:
    """Exit-criterion §14.5: N=4 free-for-all runs to completion with 4 agent dirs."""
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()

    summary_path = _run_match(DEMO_MATCH_4FFA, log_root=log_root, match_id="demo-4ffa")

    assert summary_path.exists()
    match_dir = summary_path.parent
    agents_dir = match_dir / "agents"
    assert agents_dir.is_dir(), f"agents/ missing at {agents_dir}"
    agent_subdirs = sorted(p.name for p in agents_dir.iterdir() if p.is_dir())
    assert agent_subdirs == ["00", "01", "02", "03"], (
        f"expected 4 agent dirs 00..03, got {agent_subdirs}"
    )


@pytest.mark.e2e
def test_log_completeness(tmp_path: Path) -> None:
    """Exit-criterion §14.4: S5 log completeness — every expected file exists and parses."""
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary_path = _run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1")

    match_dir = summary_path.parent
    assert (match_dir / "match.jsonl").is_file()
    assert (match_dir / "orchestrator.log").is_file()
    for slot_dir in (match_dir / "agents").iterdir():
        for jsonl in ("events.jsonl", "bash.jsonl", "api.jsonl", "context.jsonl"):
            path = slot_dir / jsonl
            assert path.is_file(), f"{path} missing"
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    json.loads(line)


def _run_match(match_config: Path, *, log_root: Path, match_id: str) -> Path:
    """Drive end-to-end via `arenabench run` subprocess; return summary.json path.

    Uses the production CLI so the test exercises the full user-facing path
    (typer entry → _drive_match → QemuVm + SshOrchestratorServer + run_match).
    """
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
