"""End-to-end test: VM disposability across matches (plan §9 S8, §14.6).

Skipped by default. Run via `ARENABENCH_E2E=1 pytest -m e2e tests/e2e/test_vm_disposability.py`.

S8 binary observable: match A creates a file at /tmp/MARKER inside its VM;
match B's `ls /tmp/MARKER` returns 'No such file or directory'; match A's
qcow2 overlay file is deleted after archive_grace_s.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_IMAGE_PATH = REPO_ROOT / "vm" / "images" / "arenabench-golden-aarch64.qcow2"


def _skip_if_no_e2e() -> None:
    if not os.environ.get("ARENABENCH_E2E"):
        pytest.skip("set ARENABENCH_E2E=1 to run real-VM e2e tests")
    if not GOLDEN_IMAGE_PATH.exists():
        pytest.skip(f"golden image not built; run vm/golden/build.sh ({GOLDEN_IMAGE_PATH} missing)")
    if not shutil.which("qemu-system-aarch64"):
        pytest.skip("qemu-system-aarch64 not on PATH")


@pytest.mark.e2e
def test_match_b_does_not_see_match_a_marker(tmp_path: Path) -> None:
    """S8: match B sees no leftover state from match A's /tmp/MARKER write.

    Stub for now — wiring requires orchestrator.cli.run to actually drive
    matches end-to-end (Wave 7+). Until then, this test documents the
    binary observable and skips.
    """
    _skip_if_no_e2e()

    pytest.skip(
        "orchestrator.cli.run is wired in a future revision; the scenario "
        "is: run match A → write /tmp/MARKER from one agent's bash → run "
        "match B → confirm /tmp/MARKER missing AND match A's overlay file "
        "deleted after archive_grace_s."
    )


@pytest.mark.e2e
def test_overlay_deleted_after_archive_grace(tmp_path: Path) -> None:
    """S8 corollary: per-match qcow2 overlay is deleted archive_grace_s after match ends."""
    _skip_if_no_e2e()

    pytest.skip("see test_match_b_does_not_see_match_a_marker — same wiring dependency")
