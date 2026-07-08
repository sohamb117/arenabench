from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal, cast

import typer

from common.clock import now_monotonic_s
from common.errors import ConfigError
from common.ids import make_match_id
from orchestrator._cli_helpers import (
    build_egress_proxy,
    detect_edk2_pflash,
    ensure_ssh_keypair,
    load_agent_blobs,
    resolve_agent_credentials,
)
from orchestrator.cloudinit import render_user_data, write_seed_iso
from orchestrator.lifecycle import MatchContext, MatchOutcome, run_match
from orchestrator.liveness import LivenessThresholds
from orchestrator.logger import MatchLogger
from orchestrator.match_config import MatchConfig, load_match_config
from orchestrator.ssh_readiness import wait_for_ssh_ready
from orchestrator.ssh_server import PROBE_PORT, SshOrchestratorServer
from orchestrator.vm import QemuConfig, QemuVm, detect_accel

app = typer.Typer(
    name="arenabench",
    help="N-LLM adversarial benchmark inside a single disposable Linux VM.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VM_IMAGES_DIR = _REPO_ROOT / "vm" / "images"
_DEFAULT_GOLDEN = _VM_IMAGES_DIR / "arenabench-golden-aarch64.qcow2"
_DEFAULT_LOG_ROOT = _REPO_ROOT / "logs"
_SSH_READY_TIMEOUT_S = 120.0
_SSH_HOST_PORT = 22222
_EXIT_CONFIG_ERROR = 1
_EXIT_RUNTIME_ERROR = 3
_ARCH_TO_IMAGE_SUFFIX: dict[str, str] = {"aarch64": "aarch64", "x86_64": "amd64"}


@app.command(name="validate")
def validate(match_path: Path) -> None:
    """Validate a match.json. Exits 0 if valid; nonzero with field violations otherwise."""
    try:
        config = load_match_config(match_path)
    except ConfigError as exc:
        field = f"field={exc.field}" if exc.field else "field=<root>"
        typer.echo(f"ERROR {exc} path={match_path} {field}", err=True)
        raise typer.Exit(code=_EXIT_CONFIG_ERROR) from exc
    typer.echo(
        f"OK match_id={config.match_id} n_agents={config.n_agents} "
        f"network_policy={config.network_policy}"
    )


@app.command(name="replay")
def replay(match_dir: Path) -> None:
    """Pretty-print the summary.json of a completed match directory."""
    summary_path = match_dir / "summary.json"
    if not summary_path.is_file():
        typer.echo(f"ERROR summary.json not found at {summary_path}", err=True)
        raise typer.Exit(code=_EXIT_CONFIG_ERROR)
    raw = summary_path.read_text(encoding="utf-8")
    summary = cast(object, json.loads(raw))
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@app.command(name="run")
def run_match_command(
    match_path: Path,
    log_root: Path = _DEFAULT_LOG_ROOT,
    golden_image: Path = _DEFAULT_GOLDEN,
    arch: str = "aarch64",
    ephemeral: bool = False,
) -> None:
    """Run a match end-to-end. Requires a built golden image + LLM credentials.

    arch must be 'aarch64' or 'x86_64'. When --golden-image is left at the
    default and --arch is x86_64, the default is rewritten to the amd64
    golden so users do not need to pass --golden-image alongside --arch.

    --ephemeral (plan §3.B3): pass `snapshot=on` to QEMU's disk0 drive and
    skip the per-match qcow2 overlay creation/cleanup. Intended for CI /
    one-shot smoke runs where the per-match overlay would be wasted IO.
    """
    if arch not in _ARCH_TO_IMAGE_SUFFIX:
        typer.echo(f"ERROR --arch must be 'aarch64' or 'x86_64', got {arch!r}", err=True)
        raise typer.Exit(code=_EXIT_CONFIG_ERROR)
    if golden_image == _DEFAULT_GOLDEN and arch != "aarch64":
        suffix = _ARCH_TO_IMAGE_SUFFIX[arch]
        golden_image = _VM_IMAGES_DIR / f"arenabench-golden-{suffix}.qcow2"
    try:
        config = load_match_config(match_path)
    except ConfigError as exc:
        typer.echo(f"ERROR config: {exc}", err=True)
        raise typer.Exit(code=_EXIT_CONFIG_ERROR) from exc
    if not golden_image.is_file():
        typer.echo(
            f"ERROR golden image missing at {golden_image}; run vm/golden/build.sh first",
            err=True,
        )
        raise typer.Exit(code=_EXIT_RUNTIME_ERROR)
    qemu_arch = cast("Literal['aarch64', 'x86_64']", arch)
    outcome = _drive_match(
        config,
        golden_image=golden_image,
        log_root=log_root,
        arch=qemu_arch,
        ephemeral=ephemeral,
    )
    typer.echo(f"DONE result={outcome.result} winner={outcome.winner} cause={outcome.cause}")


@app.command(name="build-vm")
def build_vm(arch: str = "aarch64") -> None:
    """Build the golden VM image. Shells out to vm/golden/build.sh with ARENABENCH_ARCH."""
    script = _REPO_ROOT / "vm" / "golden" / "build.sh"
    if not script.is_file():
        typer.echo(f"ERROR build script missing at {script}", err=True)
        raise typer.Exit(code=_EXIT_RUNTIME_ERROR)
    env = {**os.environ, "ARENABENCH_ARCH": arch}
    result = subprocess.run([str(script)], env=env, check=False)
    raise typer.Exit(code=result.returncode)


def _drive_match(
    config: MatchConfig,
    *,
    golden_image: Path,
    log_root: Path,
    arch: Literal["aarch64", "x86_64"] = "aarch64",
    ephemeral: bool = False,
) -> MatchOutcome:
    log_root.mkdir(parents=True, exist_ok=True)
    match_id = make_match_id(config.match_id)
    logger = MatchLogger(log_root, match_id, config.n_agents)
    overlay_dir = log_root / "matches" / config.match_id / "vm"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    config_blobs, prompt_blobs = load_agent_blobs(config.agents)
    agent_credentials = resolve_agent_credentials(config.agents)
    key_path, ssh_pubkey = ensure_ssh_keypair(overlay_dir)
    proxy = None
    vm = None
    server = None
    try:
        proxy, proxy_target = build_egress_proxy(config, overlay_dir.parent)
        user_data = render_user_data(
            match_config=config,
            config_blobs=config_blobs,
            prompt_blobs=prompt_blobs,
            ssh_pubkey=ssh_pubkey,
            agent_env_vars=agent_credentials.env_vars,
            agent_secret_files=agent_credentials.secret_files,
            proxy_target=proxy_target,
        )
        seed_iso = overlay_dir / "seed.iso"
        write_seed_iso(user_data=user_data, out_path=seed_iso)
        edk2_code, edk2_vars_src = detect_edk2_pflash() if arch == "aarch64" else (None, None)
        edk2_vars = _copy_pflash_vars(edk2_vars_src, overlay_dir) if edk2_vars_src else None
        qemu_cfg = QemuConfig(
            golden_image=golden_image,
            overlay_dir=overlay_dir,
            arch=arch,
            cid=3,
            accel=detect_accel(),
            seed_iso=seed_iso,
            edk2_code=edk2_code,
            edk2_vars=edk2_vars,
            console_log=overlay_dir / "vm-console.log",
            host_ssh_port=_SSH_HOST_PORT,
            enable_vsock=False,
            ephemeral=ephemeral,
        )
        vm = QemuVm(qemu_cfg)
        server = SshOrchestratorServer(
            agents=list(config.agents),
            ssh_host="127.0.0.1",
            ssh_port=_SSH_HOST_PORT,
            key_path=key_path,
        )
        vm.create_overlay()
        vm.start()
        wait_for_ssh_ready(
            host="127.0.0.1",
            port=_SSH_HOST_PORT,
            key_path=key_path,
            agents=list(config.agents),
            deadline_s=_SSH_READY_TIMEOUT_S,
        )
        server.start()
        ctx = MatchContext(
            match_config=config,
            vsock_server=server,
            guest_probe_port=PROBE_PORT,
            agent_ports=server.agent_ports,
            logger=logger,
            clock=now_monotonic_s,
            liveness=LivenessThresholds(),
            poll_interval_s=1.0,
            transport_used="ssh",
            cid=None,
        )
        return run_match(ctx)
    finally:
        if server is not None:
            server.stop()
        if vm is not None:
            vm.terminate()
            vm.cleanup()
        if proxy is not None:
            proxy.stop()


def _copy_pflash_vars(src: Path, overlay_dir: Path) -> Path:
    """Copy edk2 vars firmware to per-match overlay so QEMU can write to it.

    The system-installed file is typically read-only (homebrew shared share/qemu/
    or /usr/share/AAVMF/); QEMU needs a writable pflash backing file at boot.
    Mirrors vm/golden/build.sh's `cp "$EDK2_VARS" "$TMP/edk2-vars.fd"`.
    """
    dst = overlay_dir / "edk2-vars.fd"
    shutil.copy(src, dst)
    dst.chmod(0o644)
    return dst


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, standalone_mode=False)
    except typer.Exit as exc:
        return exc.exit_code
    except SystemExit as exc:
        code = exc.code
        if isinstance(code, int):
            return code
        return 0 if code is None else 1
    return 0


_ = shutil.which


if __name__ == "__main__":
    sys.exit(main())
