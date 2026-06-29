from __future__ import annotations

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
class RenderedAgent:
    slot: int
    user: str
    config_blob: str
    prompt_blob: str
    cgroup_limits: CgroupLimits | None
    env_vars: tuple[tuple[str, str], ...]


def _build_agents(
    match_config: MatchConfig,
    config_blobs: dict[int, str],
    prompt_blobs: dict[int, str],
    agent_env_vars: dict[int, tuple[tuple[str, str], ...]],
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
                env_vars=agent_env_vars.get(entry.slot, ()),
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
    template_path: Path = _DEFAULT_TEMPLATE_PATH,
) -> str:
    agents = _build_agents(
        match_config, config_blobs, prompt_blobs, agent_env_vars or {}, template_path
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
    )


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
        try:
            subprocess.run(
                ["cloud-localds", str(out_path), str(ud_path), str(md_path)],
                check=True,
                text=True,
                capture_output=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr_raw: object = cast(object, exc.stderr)
            stderr = stderr_raw if isinstance(stderr_raw, str) else ""
            raise ConfigError(
                f"cloud-localds failed: {stderr}",
                path=str(out_path),
            ) from exc
