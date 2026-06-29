import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from common.errors import LifecycleError

Arch = Literal["aarch64", "x86_64"]
Accel = Literal["hvf", "tcg", "auto"]


class QemuProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


@dataclass(frozen=True, slots=True)
class QemuConfig:
    golden_image: Path
    overlay_dir: Path
    arch: Arch
    cid: int
    smp: int = 4
    mem_mb: int = 4096
    accel: Accel = "auto"
    seed_iso: Path | None = None
    edk2_code: Path | None = None
    edk2_vars: Path | None = None
    console_log: Path | None = None
    host_ssh_port: int | None = None
    enable_vsock: bool = True
    ephemeral: bool = False


class QemuVm:
    """Lifecycle wrapper around a qemu-system-{aarch64,x86_64} subprocess."""

    def __init__(self, cfg: QemuConfig, *, qemu_binary: str | None = None) -> None:
        self._cfg = cfg
        self._qemu_binary = qemu_binary
        self._process: QemuProcess | None = None
        self._overlay = self.create_overlay_path()

    def create_overlay_path(self) -> Path:
        return self._cfg.overlay_dir / f"{self._cfg.golden_image.stem}.overlay.qcow2"

    def create_overlay(self) -> Path:
        if self._cfg.ephemeral:
            return self._overlay
        self._cfg.overlay_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(self._cfg.golden_image),
                str(self._overlay),
            ],
            check=True,
        )
        return self._overlay

    def build_argv(self) -> list[str]:
        accel = self._resolved_accel()
        argv = [
            self._binary(),
            "-machine",
            self._machine_arg(accel),
            "-cpu",
            self._cpu_arg(accel),
            "-smp",
            str(self._cfg.smp),
            "-m",
            str(self._cfg.mem_mb),
            "-display",
            "none",
            "-serial",
            self._serial_arg(),
        ]
        if self._cfg.arch == "aarch64":
            argv.extend(self._pflash_args())
        argv.extend(self._disk0_args())
        argv.extend(
            [
                "-device",
                "virtio-blk-pci,drive=disk0",
                "-device",
                "virtio-scsi-pci,id=scsi0",
            ]
        )
        if self._cfg.seed_iso is not None:
            argv.extend(
                [
                    "-drive",
                    f"if=none,file={self._cfg.seed_iso},format=raw,media=cdrom,id=seed0",
                    "-device",
                    "scsi-cd,bus=scsi0.0,drive=seed0",
                ]
            )
        if self._cfg.enable_vsock:
            argv.extend(
                [
                    "-device",
                    f"vhost-vsock-pci,guest-cid={self._cfg.cid}",
                ]
            )
        argv.extend(
            [
                "-netdev",
                self._netdev_arg(),
                "-device",
                "virtio-net-pci,netdev=net0",
            ]
        )
        return argv

    def _disk0_args(self) -> list[str]:
        if self._cfg.ephemeral:
            return [
                "-drive",
                f"if=none,file={self._cfg.golden_image},format=qcow2,id=disk0,snapshot=on",
            ]
        return [
            "-drive",
            f"if=none,file={self._overlay},format=qcow2,id=disk0",
        ]

    def _netdev_arg(self) -> str:
        if self._cfg.host_ssh_port is None:
            return "user,id=net0"
        return f"user,id=net0,hostfwd=tcp::{self._cfg.host_ssh_port}-:22"

    def start(self) -> None:
        if self.is_running():
            raise LifecycleError("QEMU already running", state="running", event="start")
        self._process = subprocess.Popen(self.build_argv())
        if self._process.poll() is not None:
            raise LifecycleError("QEMU exited during start", state="exited", event="start")

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def terminate(self, grace_s: float = 5.0) -> None:
        if self._process is None:
            return
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()

    def wait(self, timeout_s: float) -> int | None:
        if self._process is None:
            raise LifecycleError("QEMU not started", state="stopped", event="wait")
        try:
            return self._process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None

    def cleanup(self) -> None:
        if self._cfg.ephemeral:
            return
        self._overlay.unlink(missing_ok=True)

    @property
    def pid(self) -> int | None:
        if self._process is None:
            return None
        return self._process.pid

    def _binary(self) -> str:
        if self._qemu_binary is not None:
            return self._qemu_binary
        return f"qemu-system-{self._cfg.arch}"

    def _resolved_accel(self) -> Literal["hvf", "tcg"]:
        if self._cfg.accel == "auto":
            return detect_accel()
        return self._cfg.accel

    def _machine_arg(self, accel: Literal["hvf", "tcg"]) -> str:
        if self._cfg.arch == "aarch64":
            return f"virt,accel={accel}"
        return f"q35,accel={accel}"

    def _cpu_arg(self, accel: Literal["hvf", "tcg"]) -> str:
        if accel == "tcg":
            return "max"
        return "host"

    def _serial_arg(self) -> str:
        if self._cfg.console_log is None:
            return "mon:stdio"
        return f"file:{self._cfg.console_log}"

    def _pflash_args(self) -> list[str]:
        if self._cfg.edk2_code is None or self._cfg.edk2_vars is None:
            raise LifecycleError(
                "missing aarch64 pflash paths", state="configured", event="build_argv"
            )
        return [
            "-drive",
            f"if=pflash,format=raw,readonly=on,file={self._cfg.edk2_code}",
            "-drive",
            f"if=pflash,format=raw,file={self._cfg.edk2_vars}",
        ]


def detect_accel() -> Literal["hvf", "tcg"]:
    result = subprocess.run(
        ["sysctl", "-n", "kern.hv_support"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip() == "1":
        return "hvf"
    return "tcg"
