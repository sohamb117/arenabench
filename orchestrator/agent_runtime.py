from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import litellm

from common.errors import ConfigError
from harness.config import AgentConfig, load_config
from orchestrator.match_config import AgentEntry

type AgentLoader = Callable[[Path], AgentConfig]
type MetadataLoader = Callable[[str], Mapping[str, object]]


def resolve_context_limits(
    agents: list[AgentEntry],
    *,
    load_agent: AgentLoader = load_config,
    load_metadata: MetadataLoader = litellm.get_model_info,
    repo_root: Path,
) -> dict[int, int]:
    limits: dict[int, int] = {}
    for entry in agents:
        config = load_agent(repo_root / entry.config)
        if config.max_context_tokens is not None:
            limits[entry.slot] = config.max_context_tokens
            continue
        lookup_model = config.model
        if lookup_model.startswith("github_copilot/"):
            lookup_model = lookup_model.removeprefix("github_copilot/").removeprefix("responses/")
        metadata = load_metadata(lookup_model)
        raw_limit = metadata.get("max_input_tokens")
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit < 1:
            raise ConfigError(
                "LiteLLM model metadata requires a positive max_input_tokens",
                path=entry.config,
                field="max_context_tokens",
            )
        limits[entry.slot] = raw_limit
    return limits


def resolve_output_limits(
    agents: list[AgentEntry],
    *,
    load_agent: AgentLoader = load_config,
    load_metadata: MetadataLoader = litellm.get_model_info,
    repo_root: Path,
) -> dict[int, int]:
    limits: dict[int, int] = {}
    for entry in agents:
        config = load_agent(repo_root / entry.config)
        lookup_model = config.model
        if lookup_model.startswith("github_copilot/"):
            lookup_model = lookup_model.removeprefix("github_copilot/").removeprefix("responses/")
        metadata = load_metadata(lookup_model)
        raw_limit = metadata.get("max_output_tokens")
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit < 1:
            raise ConfigError(
                "LiteLLM model metadata requires a positive max_output_tokens",
                path=entry.config,
                field="model",
            )
        limits[entry.slot] = raw_limit
    return limits
