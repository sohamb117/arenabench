from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Literal, cast

import typer

from common.errors import ConfigError
from orchestrator.match_config import load_match_config
from orchestrator.match_driver import drive_match
from orchestrator.match_validation import check_referenced_files_exist

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
_EXIT_CONFIG_ERROR = 1
_EXIT_RUNTIME_ERROR = 3
_ARCH_TO_IMAGE_SUFFIX: dict[str, str] = {"aarch64": "aarch64", "x86_64": "amd64"}


@app.command(name="validate")
def validate(match_path: Path) -> None:
    """Validate a match.json. Exits 0 if valid; nonzero with field violations otherwise."""
    try:
        config = load_match_config(match_path)
        check_referenced_files_exist(config, _REPO_ROOT)
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
    outcome = drive_match(
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


if __name__ == "__main__":
    sys.exit(main())
