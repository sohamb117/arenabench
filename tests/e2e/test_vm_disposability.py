"""End-to-end test: VM disposability across matches (plan §9 S8, §14.6).

Skipped by default. Run via `ARENABENCH_E2E=1 pytest -m e2e tests/e2e/test_vm_disposability.py`.

S8 binary observable: match A creates a file at /tmp/MARKER inside its VM;
match B's `ls /tmp/MARKER` returns 'No such file or directory'; match A's
qcow2 overlay file is deleted after archive_grace_s.

Implementation: each match runs synchronously (uv run arenabench run) in a
worker thread; the main thread polls for the per-match ssh_key + the host
SSH port (22222) and SSHes into the VM to either inject /tmp/MARKER (match A)
or verify its absence (match B). Match A and match B use distinct match_ids
(s8-match-a, s8-match-b) so their overlay directories are independent.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_IMAGE_PATH = REPO_ROOT / "vm" / "images" / "arenabench-golden-aarch64.qcow2"
DEMO_MATCH_1V1 = REPO_ROOT / "configs" / "matches" / "demo-1v1.json"
S8_MATCH_A = REPO_ROOT / "configs" / "matches" / "s8-match-a.json"
S8_MATCH_B = REPO_ROOT / "configs" / "matches" / "s8-match-b.json"
SSH_HOST_PORT = 22222
SSH_READY_TIMEOUT_S = 300.0


def _skip_if_no_e2e() -> None:
    if not os.environ.get("ARENABENCH_E2E"):
        pytest.skip("set ARENABENCH_E2E=1 to run real-VM e2e tests")
    if not GOLDEN_IMAGE_PATH.exists():
        pytest.skip(f"golden image not built; run vm/golden/build.sh ({GOLDEN_IMAGE_PATH} missing)")
    if not shutil.which("qemu-system-aarch64"):
        pytest.skip("qemu-system-aarch64 not on PATH")
    if not shutil.which("ssh"):
        pytest.skip("ssh not on PATH")


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


def _wait_for_ssh(key_path: Path) -> bool:
    deadline = time.monotonic() + SSH_READY_TIMEOUT_S
    while time.monotonic() < deadline:
        if key_path.is_file():
            try:
                with socket.create_connection(("127.0.0.1", SSH_HOST_PORT), timeout=1.0):
                    return True
            except OSError:
                pass
        time.sleep(1.0)
    return False


def _ssh_run(key_path: Path, command: str) -> tuple[int, str]:
    result = subprocess.run(
        [
            "ssh",
            "-i",
            str(key_path),
            "-p",
            str(SSH_HOST_PORT),
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "root@127.0.0.1",
            command,
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return result.returncode, result.stdout + result.stderr


@pytest.mark.e2e
def test_match_b_does_not_see_match_a_marker(tmp_path: Path) -> None:
    """S8 + §14.6 binary observable: match A's /tmp/MARKER MUST NOT appear in match B's VM."""
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()

    inject_result: dict[str, object] = {}

    def _inject_marker_in_a() -> None:
        key = log_root / "matches" / "s8-match-a" / "vm" / "ssh_key"
        if not _wait_for_ssh(key):
            inject_result["error"] = "ssh not ready for match A"
            return
        rc, out = _ssh_run(key, "touch /tmp/MARKER && ls -la /tmp/MARKER")
        inject_result["rc"] = rc
        inject_result["out"] = out

    t_a = threading.Thread(target=_inject_marker_in_a, daemon=True)
    t_a.start()
    summary_a = _run_match(S8_MATCH_A, log_root=log_root, match_id="s8-match-a")
    t_a.join(timeout=10.0)

    if "error" in inject_result:
        pytest.fail(f"S8 setup: {inject_result['error']}")
    assert inject_result.get("rc") == 0, f"MARKER inject failed: {inject_result.get('out')}"

    check_result: dict[str, object] = {}

    def _check_marker_in_b() -> None:
        key = log_root / "matches" / "s8-match-b" / "vm" / "ssh_key"
        if not _wait_for_ssh(key):
            check_result["error"] = "ssh not ready for match B"
            return
        rc, out = _ssh_run(key, "test -f /tmp/MARKER; echo MARKER_PRESENT=$?")
        check_result["rc"] = rc
        check_result["out"] = out

    t_b = threading.Thread(target=_check_marker_in_b, daemon=True)
    t_b.start()
    summary_b = _run_match(S8_MATCH_B, log_root=log_root, match_id="s8-match-b")
    t_b.join(timeout=10.0)

    if "error" in check_result:
        pytest.fail(f"S8 check: {check_result['error']}")
    out_b = str(check_result.get("out", ""))
    assert "MARKER_PRESENT=1" in out_b, (
        f"S8 §14.6 violation: match B saw /tmp/MARKER from match A. check output: {out_b}"
    )

    raw_a = cast(dict[str, object], json.loads(summary_a.read_text(encoding="utf-8")))
    raw_b = cast(dict[str, object], json.loads(summary_b.read_text(encoding="utf-8")))
    assert raw_a["final_state"] == "DONE"
    assert raw_b["final_state"] == "DONE"


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
