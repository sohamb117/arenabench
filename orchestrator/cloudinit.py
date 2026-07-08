from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import jinja2

from common.errors import ConfigError
from orchestrator.match_config import CgroupLimits, MatchConfig

_DEFAULT_TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent / "vm" / "cloud-init" / "user-data.j2"
)
_DEFAULT_META_DATA = "instance-id: arenabench/local-test\nlocal-hostname: arenabench\n"


@dataclass(frozen=True, slots=True)
class SecretFile:
    path: str
    content: str


@dataclass(frozen=True, slots=True)
class RenderedAgent:
    slot: int
    user: str
    config_blob: str
    prompt_blob: str
    cgroup_limits: CgroupLimits | None
    env_vars: tuple[tuple[str, str], ...]
    secret_files: tuple[SecretFile, ...]


@dataclass(frozen=True, slots=True)
class GuestProxyTarget:
    guest_addr: str
    port: int


def _build_agents(
    match_config: MatchConfig,
    config_blobs: dict[int, str],
    prompt_blobs: dict[int, str],
    agent_env_vars: dict[int, tuple[tuple[str, str], ...]],
    agent_secret_files: dict[int, tuple[SecretFile, ...]],
    template_path: Path,
) -> list[RenderedAgent]:
    agents: list[RenderedAgent] = []
    for entry in sorted(match_config.agents, key=lambda a: a.slot):
        if entry.slot not in config_blobs:
            raise ConfigError(
                f"missing config blob for slot {entry.slot}",
                path=str(template_path),
                field=f"config_blobs[{entry.slot}]",
            )
        if entry.slot not in prompt_blobs:
            raise ConfigError(
                f"missing prompt blob for slot {entry.slot}",
                path=str(template_path),
                field=f"prompt_blobs[{entry.slot}]",
            )
        agents.append(
            RenderedAgent(
                slot=entry.slot,
                user=entry.user,
                config_blob=config_blobs[entry.slot],
                prompt_blob=prompt_blobs[entry.slot],
                cgroup_limits=match_config.cgroup_limits,
                env_vars=tuple((k, shlex.quote(v)) for k, v in agent_env_vars.get(entry.slot, ())),
                secret_files=agent_secret_files.get(entry.slot, ()),
            )
        )
    return agents


def render_user_data(
    *,
    match_config: MatchConfig,
    config_blobs: dict[int, str],
    prompt_blobs: dict[int, str],
    ssh_pubkey: str,
    agent_env_vars: dict[int, tuple[tuple[str, str], ...]] | None = None,
    agent_secret_files: dict[int, tuple[SecretFile, ...]] | None = None,
    proxy_target: GuestProxyTarget | None = None,
    template_path: Path = _DEFAULT_TEMPLATE_PATH,
) -> str:
    agents = _build_agents(
        match_config,
        config_blobs,
        prompt_blobs,
        agent_env_vars or {},
        agent_secret_files or {},
        template_path,
    )
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(template_path.parent),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=jinja2.StrictUndefined,
    )
    template = env.get_template(template_path.name)
    return template.render(
        agents=agents,
        network_policy=match_config.network_policy,
        ssh_pubkey=ssh_pubkey,
        proxy_target=proxy_target,
    )


def _seed_iso_argv(out_path: Path, stage: Path, ud_path: Path, md_path: Path) -> list[str] | None:
    """First-available seed-iso builder, in order of preference.

    cloud-init NoCloud datasource requires: ISO9660 + Rock Ridge + Joliet,
    volume label `cidata`, user-data + meta-data at filesystem root. All four
    builders below honor that contract; cloud-localds takes the two files
    directly, the others take a pre-populated staging directory.
    """
    if shutil.which("cloud-localds") is not None:
        return ["cloud-localds", str(out_path), str(ud_path), str(md_path)]
    iso_args = ["-output", str(out_path), "-volid", "cidata", "-joliet", "-rock", str(stage)]
    if shutil.which("mkisofs") is not None:
        return ["mkisofs", "-quiet", *iso_args]
    if shutil.which("genisoimage") is not None:
        return ["genisoimage", "-quiet", *iso_args]
    if shutil.which("hdiutil") is not None:
        return [
            "hdiutil",
            "makehybrid",
            "-quiet",
            "-iso",
            "-joliet",
            "-default-volume-name",
            "cidata",
            "-o",
            str(out_path),
            str(stage),
        ]
    return None


def write_seed_iso(
    *,
    user_data: str,
    out_path: Path,
    meta_data: str = _DEFAULT_META_DATA,
) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        ud_path = tmp / "user-data"
        md_path = tmp / "meta-data"
        ud_path.write_text(user_data, encoding="utf-8")
        md_path.write_text(meta_data, encoding="utf-8")
        argv = _seed_iso_argv(out_path, tmp, ud_path, md_path)
        if argv is None:
            raise ConfigError(
                "no seed-iso builder found on PATH "
                "(install one of: cloud-localds | mkisofs | genisoimage | hdiutil)",
                path=str(out_path),
            )
        try:
            subprocess.run(argv, check=True, text=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            stderr_raw: object = cast(object, exc.stderr)
            stderr = stderr_raw if isinstance(stderr_raw, str) else ""
            raise ConfigError(
                f"seed-iso builder {argv[0]} failed: {stderr}",
                path=str(out_path),
            ) from exc
