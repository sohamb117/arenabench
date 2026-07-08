from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from common.clock import now_monotonic_s
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
from orchestrator.match_config import MatchConfig
from orchestrator.match_validation import check_referenced_files_exist
from orchestrator.ssh_readiness import wait_for_ssh_ready
from orchestrator.ssh_server import PROBE_PORT, SshOrchestratorServer
from orchestrator.vm import QemuConfig, QemuVm, detect_accel

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SSH_READY_TIMEOUT_S = 120.0
_SSH_HOST_PORT = 22222


def drive_match(
    config: MatchConfig,
    *,
    golden_image: Path,
    log_root: Path,
    arch: Literal["aarch64", "x86_64"] = "aarch64",
    ephemeral: bool = False,
) -> MatchOutcome:
    """Boot the VM, provision agents, run the match, and tear everything down.

    Wires the full runtime: renders the cloud-init seed-iso from the agent
    config/prompt blobs + resolved credentials, starts the host-side egress
    proxy (allowlist mode), boots QEMU over the per-match qcow2 overlay, waits
    for cloud-init + per-agent files, opens the SSH transport, and drives
    orchestrator.lifecycle.run_match. The finally block always stops the
    server, terminates + cleans the VM, and stops the proxy.
    """
    check_referenced_files_exist(config, _REPO_ROOT)
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
