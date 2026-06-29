import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from common.errors import LifecycleError
from orchestrator.vm import QemuConfig, QemuVm

_Arch = Literal["aarch64", "x86_64"]

SMP = 4
MEM_MB = 4096
CID = 42
TERM_TIMEOUT_S = 0.01
E2E_RUNTIME_S = 30.0


class FakeQemuProcess:
    def __init__(self, exit_after_wait: int | None = None) -> None:
        self.pid = 1234
        self._returncode: int | None = None
        self._exit_after_wait = exit_after_wait
        self.terminated = False
        self.killed = False
        self.wait_timeouts: list[float | None] = []

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self._returncode is not None:
            return self._returncode
        if self._exit_after_wait is None:
            raise subprocess.TimeoutExpired(cmd="qemu", timeout=0.0 if timeout is None else timeout)
        self._returncode = self._exit_after_wait
        return self._returncode


def _cfg(tmp_path: Path, arch: _Arch = "aarch64") -> QemuConfig:
    return QemuConfig(
        golden_image=tmp_path / "golden.qcow2",
        overlay_dir=tmp_path / "overlay",
        arch=arch,
        smp=SMP,
        mem_mb=MEM_MB,
        accel="hvf" if arch == "aarch64" else "tcg",
        cid=CID,
        seed_iso=tmp_path / "seed.iso",
        edk2_code=tmp_path / "edk2-code.fd",
        edk2_vars=tmp_path / "edk2-vars.fd",
    )


def test_build_argv_constructs_aarch64_hvf_command(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    vm = QemuVm(cfg)
    overlay = vm.create_overlay_path()

    argv = vm.build_argv()

    assert argv[0] == "qemu-system-aarch64"
    assert argv[argv.index("-machine") + 1] == "virt,accel=hvf"
    assert argv[argv.index("-cpu") + 1] == "host"
    assert str(SMP) == argv[argv.index("-smp") + 1]
    assert str(MEM_MB) == argv[argv.index("-m") + 1]
    assert f"if=pflash,format=raw,readonly=on,file={cfg.edk2_code}" in argv
    assert f"if=pflash,format=raw,file={cfg.edk2_vars}" in argv
    assert f"if=none,file={overlay},format=qcow2,id=disk0" in argv
    assert f"if=none,file={cfg.seed_iso},format=raw,media=cdrom,id=seed0" in argv
    assert "vhost-vsock-pci,guest-cid=42" in argv
    assert "virtio-net-pci,netdev=net0" in argv


def test_build_argv_constructs_x86_64_tcg_command(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, arch="x86_64")
    vm = QemuVm(cfg)

    argv = vm.build_argv()

    assert argv[0] == "qemu-system-x86_64"
    assert argv[argv.index("-machine") + 1] == "q35,accel=tcg"
    assert argv[argv.index("-cpu") + 1] == "max"
    assert not any("pflash" in token for token in argv)


def test_build_argv_omits_vsock_when_disabled(tmp_path: Path) -> None:
    """SSH-only path on hosts without /dev/vhost-vsock must not crash QEMU at boot."""
    cfg_dict = _cfg(tmp_path)
    cfg = QemuConfig(
        golden_image=cfg_dict.golden_image,
        overlay_dir=cfg_dict.overlay_dir,
        arch=cfg_dict.arch,
        cid=cfg_dict.cid,
        smp=cfg_dict.smp,
        mem_mb=cfg_dict.mem_mb,
        accel=cfg_dict.accel,
        seed_iso=cfg_dict.seed_iso,
        edk2_code=cfg_dict.edk2_code,
        edk2_vars=cfg_dict.edk2_vars,
        enable_vsock=False,
    )

    argv = QemuVm(cfg).build_argv()

    assert not any("vhost-vsock" in token for token in argv)


def test_create_overlay_calls_qemu_img(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], *, check: bool) -> SimpleNamespace:
        calls.append(argv)
        assert check is True
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    cfg = _cfg(tmp_path)

    overlay = QemuVm(cfg).create_overlay()

    assert calls == [
        [
            "qemu-img",
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            str(cfg.golden_image),
            str(overlay),
        ]
    ]


def test_start_calls_popen_with_constructed_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    popen_calls: list[list[str]] = []
    fake_process = FakeQemuProcess()

    def fake_popen(argv: list[str]) -> FakeQemuProcess:
        popen_calls.append(argv)
        return fake_process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    vm = QemuVm(_cfg(tmp_path))

    vm.start()

    assert popen_calls == [vm.build_argv()]
    assert vm.pid == fake_process.pid


def test_terminate_sends_sigterm_then_sigkill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_process = FakeQemuProcess()

    def fake_popen(argv: list[str]) -> FakeQemuProcess:
        return fake_process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    vm = QemuVm(_cfg(tmp_path))
    vm.start()

    vm.terminate(grace_s=TERM_TIMEOUT_S)

    assert fake_process.terminated is True
    assert fake_process.killed is True
    assert fake_process.wait_timeouts == [TERM_TIMEOUT_S, None]


def test_cleanup_deletes_overlay_idempotently(tmp_path: Path) -> None:
    vm = QemuVm(_cfg(tmp_path))
    overlay = vm.create_overlay_path()
    overlay.parent.mkdir(parents=True)
    overlay.write_text("qcow2")

    vm.cleanup()
    vm.cleanup()

    assert not overlay.exists()


def test_wait_without_start_raises(tmp_path: Path) -> None:
    with pytest.raises(LifecycleError) as exc:
        QemuVm(_cfg(tmp_path)).wait(timeout_s=TERM_TIMEOUT_S)

    assert exc.value.state == "stopped"
    assert exc.value.event == "wait"
