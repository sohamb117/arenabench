from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import typer

from common.errors import ConfigError
from orchestrator.match_config import load_match_config

app = typer.Typer(
    name="arenabench",
    help="N-LLM adversarial benchmark inside a single disposable Linux VM.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


@app.command(name="validate")
def validate(match_path: Path) -> None:
    """Validate a match.json. Exits 0 if valid; nonzero with field violations otherwise."""
    try:
        config = load_match_config(match_path)
    except ConfigError as exc:
        field = f"field={exc.field}" if exc.field else "field=<root>"
        typer.echo(f"ERROR {exc} path={match_path} {field}", err=True)
        raise typer.Exit(code=1) from exc
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
        raise typer.Exit(code=1)
    raw = summary_path.read_text(encoding="utf-8")
    summary = cast(object, json.loads(raw))
    typer.echo(json.dumps(summary, indent=2, sort_keys=True))


@app.command(name="run")
def run_match_command(match_path: Path) -> None:
    """Run a match end-to-end. Requires Wave 6 VM image — not wired in v0."""
    try:
        config = load_match_config(match_path)
    except ConfigError as exc:
        typer.echo(f"ERROR {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(
        f"NOT YET WIRED: would run match_id={config.match_id} n_agents={config.n_agents}.\n"
        f"Wave 6 wires the QEMU + vsock_server + cloud-init + state machine here.",
        err=True,
    )
    raise typer.Exit(code=2)


@app.command(name="build-vm")
def build_vm() -> None:
    """Build the golden VM image (Wave 6). Currently a stub."""
    typer.echo(
        "NOT YET WIRED: Wave 6 invokes vm/golden/build.sh here.\n"
        "Until then, see vm/golden/ for the planned image-build scripts.",
        err=True,
    )
    raise typer.Exit(code=2)


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
