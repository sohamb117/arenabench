"""detect_accel tests + real-VM e2e — split from test_vm.py for the 250 LOC cap."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.vm import QemuConfig, QemuVm, detect_accel

CID = 42
TERM_TIMEOUT_S = 0.01
E2E_RUNTIME_S = 30.0


def test_detect_accel_returns_hvf_when_sysctl_reports_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        argv: list[str], *, check: bool, capture_output: bool, text: bool
    ) -> SimpleNamespace:
        assert argv == ["sysctl", "-n", "kern.hv_support"]
        assert check is False
        assert capture_output is True
        assert text is True
        return SimpleNamespace(returncode=0, stdout="1\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert detect_accel() == "hvf"


def test_detect_accel_returns_tcg_otherwise(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(
        argv: list[str], *, check: bool, capture_output: bool, text: bool
    ) -> SimpleNamespace:
        _ = argv
        _ = check
        _ = capture_output
        _ = text
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert detect_accel() == "tcg"


@pytest.mark.e2e
def test_boots_real_golden_image(tmp_path: Path) -> None:
    if os.environ.get("ARENABENCH_E2E") != "1":
        pytest.skip("set ARENABENCH_E2E=1 and GOLDEN_IMAGE to boot a real VM")
    golden = os.environ.get("GOLDEN_IMAGE")
    if golden is None:
        pytest.skip("GOLDEN_IMAGE is required for QEMU e2e")
    cfg = QemuConfig(
        golden_image=Path(golden),
        overlay_dir=tmp_path,
        arch="aarch64",
        accel=detect_accel(),
        cid=CID,
    )
    vm = QemuVm(cfg)
    vm.create_overlay()
    vm.start()
    try:
        _ = vm.wait(timeout_s=E2E_RUNTIME_S)
    finally:
        vm.terminate(grace_s=TERM_TIMEOUT_S)
        vm.cleanup()
