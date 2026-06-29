from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import typer

from common.clock import now_monotonic_s
from common.errors import ConfigError
from common.ids import make_match_id
from orchestrator.lifecycle import MatchContext, MatchOutcome, run_match
from orchestrator.liveness import LivenessThresholds
from orchestrator.logger import MatchLogger
from orchestrator.match_config import MatchConfig, load_match_config
from orchestrator.ssh_server import PROBE_PORT, SshOrchestratorServer
from orchestrator.vm import QemuConfig, QemuVm, detect_accel

app = typer.Typer(
    name="arenabench",
    help="N-LLM adversarial benchmark inside a single disposable Linux VM.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_GOLDEN = _REPO_ROOT / "vm" / "images" / "arenabench-golden-aarch64.qcow2"
_DEFAULT_LOG_ROOT = _REPO_ROOT / "logs"
_SSH_READY_TIMEOUT_S = 120.0
_SSH_HOST_PORT = 22222
_EXIT_CONFIG_ERROR = 1
_EXIT_RUNTIME_ERROR = 3


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
) -> None:
    """Run a match end-to-end. Requires a built golden image + LLM credentials."""
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
    outcome = _drive_match(config, golden_image=golden_image, log_root=log_root)
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


def _drive_match(config: MatchConfig, *, golden_image: Path, log_root: Path) -> MatchOutcome:
    log_root.mkdir(parents=True, exist_ok=True)
    match_id = make_match_id(config.match_id)
    logger = MatchLogger(log_root, match_id, config.n_agents)
    overlay_dir = log_root / "matches" / config.match_id / "vm"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    qemu_cfg = QemuConfig(
        golden_image=golden_image,
        overlay_dir=overlay_dir,
        arch="aarch64",
        cid=3,
        accel=detect_accel(),
        console_log=overlay_dir / "vm-console.log",
    )
    vm = QemuVm(qemu_cfg)
    server = SshOrchestratorServer(
        agents=list(config.agents),
        ssh_host="127.0.0.1",
        ssh_port=_SSH_HOST_PORT,
    )
    try:
        vm.create_overlay()
        vm.start()
        _wait_for_ssh("127.0.0.1", _SSH_HOST_PORT, _SSH_READY_TIMEOUT_S)
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
        )
        return run_match(ctx)
    finally:
        server.stop()
        vm.terminate()
        vm.cleanup()


def _wait_for_ssh(host: str, port: int, timeout_s: float) -> None:
    deadline = now_monotonic_s() + timeout_s
    while now_monotonic_s() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return
        except OSError:
            time.sleep(1.0)
    raise TimeoutError(f"ssh on {host}:{port} not reachable within {timeout_s}s")


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


_ = shutil.which  # keep imported for future probe extensions


if __name__ == "__main__":
    sys.exit(main())
