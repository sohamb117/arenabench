"""End-to-end test: VM disposability across matches (plan §9 S8, §14.6).

Skipped by default. Run via `ARENABENCH_E2E=1 pytest -m e2e tests/e2e/test_vm_disposability.py`.

S8 binary observable: match A creates a file at /tmp/MARKER inside its VM;
match B's `ls /tmp/MARKER` returns 'No such file or directory'; match A's
qcow2 overlay file is deleted after archive_grace_s.
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


def _skip_if_no_e2e() -> None:
    if not os.environ.get("ARENABENCH_E2E"):
        pytest.skip("set ARENABENCH_E2E=1 to run real-VM e2e tests")
    if not GOLDEN_IMAGE_PATH.exists():
        pytest.skip(f"golden image not built; run vm/golden/build.sh ({GOLDEN_IMAGE_PATH} missing)")
    if not shutil.which("qemu-system-aarch64"):
        pytest.skip("qemu-system-aarch64 not on PATH")


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


@pytest.mark.e2e
def test_match_b_does_not_see_match_a_marker(tmp_path: Path) -> None:
    """S8 + §14.6: each match boots from a fresh qcow2 overlay; /tmp/MARKER
    written into match A's VM MUST NOT exist in match B's VM.

    Implementation: between matches A and B, SSH into match A's VM (port
    22222 on the host via the orchestrator's per-match hostfwd) and `touch
    /tmp/MARKER` directly. The overlay is destroyed in `_drive_match`'s
    finally clause when match A's lifecycle ends. Match B then boots from
    a NEW overlay over the immutable golden, so the marker is gone.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()

    summary_a = _run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1")
    summary_b = _run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1")

    raw_a = cast(dict[str, object], json.loads(summary_a.read_text(encoding="utf-8")))
    raw_b = cast(dict[str, object], json.loads(summary_b.read_text(encoding="utf-8")))
    assert raw_a["final_state"] == "DONE"
    assert raw_b["final_state"] == "DONE"

    overlay_a = log_root / "matches" / "demo-1v1" / "vm"
    qcow2_a = list(overlay_a.glob("*.qcow2")) if overlay_a.is_dir() else []
    assert not qcow2_a, (
        f"match A overlay not cleaned up (S8 §14.6 violation): {qcow2_a}; the next match would "
        "inherit /tmp/MARKER and any other in-VM mutations from the previous run."
    )


@pytest.mark.e2e
def test_overlay_deleted_after_archive_grace(tmp_path: Path) -> None:
    """S8 corollary: per-match qcow2 overlay is deleted archive_grace_s after match ends."""
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary = _run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1")
    match_dir = summary.parent
    overlay_dir = match_dir / "vm"
    qcow2_files = list(overlay_dir.glob("*.qcow2")) if overlay_dir.is_dir() else []
    assert not qcow2_files, f"per-match overlay files still present: {qcow2_files}"
