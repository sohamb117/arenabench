from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Literal, cast

import typer
from pydantic import ValidationError

from common.errors import ConfigError
from orchestrator.match_config import MatchConfig, load_match_config
from orchestrator.match_driver import drive_match
from orchestrator.match_generator import build_match_config_dict
from orchestrator.match_validation import check_referenced_files_exist
from orchestrator.transcript import TranscriptError, TranscriptFormat, read_transcript
from orchestrator.transcript_render import render_text, sanitize_text

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
        typer.echo(f"ERROR {exc}", err=True)
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


@app.command(name="transcript")
def transcript_command(
    match_dir: Path,
    agent: Annotated[int | None, typer.Option("--agent")] = None,
    turn: Annotated[int | None, typer.Option("--turn")] = None,
    output_format: Annotated[str, typer.Option("--format")] = "text",
    legacy_run: Annotated[int | None, typer.Option("--legacy-run")] = None,
) -> None:
    try:
        parsed_format = TranscriptFormat(output_format)
        document = read_transcript(
            match_dir,
            agent=agent,
            turn=turn,
            legacy_run=legacy_run,
        )
    except (TranscriptError, ValidationError, ValueError) as exc:
        safe_error = sanitize_text(str(exc)).replace("\n", " ")[:512]
        typer.echo(f"ERROR {safe_error}", err=True)
        raise typer.Exit(code=_EXIT_CONFIG_ERROR) from exc
    match parsed_format:
        case TranscriptFormat.TEXT:
            typer.echo(render_text(document), nl=False)
        case TranscriptFormat.JSON:
            typer.echo(document.model_dump_json(indent=2))


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

    --ephemeral passes `snapshot=on` to QEMU's disk0 drive and skips the
    per-match qcow2 overlay creation/cleanup. Intended for CI / one-shot smoke
    runs where the per-match overlay would be wasted IO.
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
    try:
        outcome = drive_match(
            config,
            golden_image=golden_image,
            log_root=log_root,
            arch=qemu_arch,
            ephemeral=ephemeral,
        )
    except ConfigError as exc:
        typer.echo(f"ERROR {exc}", err=True)
        raise typer.Exit(code=_EXIT_CONFIG_ERROR) from exc
    done = f"DONE result={outcome.result} winner={outcome.winner} cause={outcome.cause}"
    if outcome.budget_usd is not None or outcome.per_agent_budget_usd is not None:
        done = f"{done} spend=${outcome.estimated_spend_usd:.9f}"
    typer.echo(done)


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


@app.command(name="new-match")
def new_match(
    agent: Annotated[
        list[Path], typer.Option("--agent", "-a", help="Agent config path (repeat 2-16x)")
    ],
    match_id: Annotated[str, typer.Option("--match-id", help="Match slug (letters/digits/-/_)")],
    out: Annotated[
        Path | None, typer.Option("--out", "-o", help="Write JSON here; default is stdout")
    ] = None,
    heartbeat: Annotated[int, typer.Option("--heartbeat", help="Heartbeat cadence (s)")] = 120,
    grace: Annotated[int, typer.Option("--grace", help="Winner grace period (s)")] = 30,
    max_duration: Annotated[
        int, typer.Option("--max-duration", help="Max match duration (s)")
    ] = 1800,
    archive_grace: Annotated[int, typer.Option("--archive-grace", help="Archive grace (s)")] = 60,
    network_policy: Annotated[
        str, typer.Option("--network-policy", help="allowlist | full")
    ] = "allowlist",
    allowlist_extra: Annotated[
        list[str] | None, typer.Option("--allowlist-extra", help="Extra allowlisted domain")
    ] = None,
    budget_usd: Annotated[
        float | None, typer.Option("--budget-usd", help="Optional match-wide managed-call cap")
    ] = None,
    per_agent_budget_usd: Annotated[
        float | None,
        typer.Option("--per-agent-budget-usd", help="Optional per-agent managed-call cap"),
    ] = None,
) -> None:
    """Generate a match.json from agent configs, auto-assigning slots + users.

    Slots 0..N-1 and users agent0..agentN-1 are derived from --agent order, so a
    large free-for-all is one command. Emits to --out (or stdout) after
    validating against the match schema.
    """
    try:
        extra = tuple(allowlist_extra) if allowlist_extra else None
        payload = build_match_config_dict(
            agent_paths=agent,
            match_id=match_id,
            heartbeat_interval_s=heartbeat,
            grace_period_s=grace,
            max_duration_s=max_duration,
            archive_grace_s=archive_grace,
            network_policy=network_policy,
            domain_allowlist_extra=extra,
            budget_usd=budget_usd,
            per_agent_budget_usd=per_agent_budget_usd,
        )
        config = MatchConfig.model_validate(payload)
        check_referenced_files_exist(config, _REPO_ROOT)
    except (ConfigError, ValueError) as exc:
        typer.echo(f"ERROR {exc}", err=True)
        raise typer.Exit(code=_EXIT_CONFIG_ERROR) from exc
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
        typer.echo(f"OK wrote {out} match_id={config.match_id} n_agents={config.n_agents}")
    else:
        typer.echo(rendered)


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
