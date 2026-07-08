from __future__ import annotations

from pathlib import Path

from common.errors import ConfigError
from harness.config import load_config, read_system_prompt
from orchestrator.match_config import MatchConfig


def check_referenced_files_exist(config: MatchConfig, base_dir: Path) -> None:
    """Verify every file a MatchConfig references resolves on disk.

    For each agent, resolved relative to *base_dir* (the repo root): its config
    file must exist and parse as an AgentConfig, and that agent's
    system_prompt_path must resolve to a non-empty file. Raises ConfigError with
    field ``agents[<slot>].config`` or ``agents[<slot>].system_prompt_path`` on
    the first miss, so ``validate`` and ``run`` fail early with a precise pointer
    instead of deep inside cloud-init rendering.
    """
    for agent in config.agents:
        agent_path = base_dir / agent.config
        if not agent_path.is_file():
            raise ConfigError(
                f"referenced agent config missing: {agent_path}",
                path=str(agent_path),
                field=f"agents[{agent.slot}].config",
            )
        try:
            agent_cfg = load_config(agent_path)
        except ConfigError as exc:
            raise ConfigError(
                str(exc), path=str(agent_path), field=f"agents[{agent.slot}].config"
            ) from exc
        try:
            _ = read_system_prompt(agent_cfg, base_dir)
        except ConfigError as exc:
            raise ConfigError(
                str(exc), path=exc.path, field=f"agents[{agent.slot}].system_prompt_path"
            ) from exc
