from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from common.errors import ConfigError
from harness.config import AgentConfig, load_config
from orchestrator.budget_runtime import BudgetRuntime, SlotBudgetPolicy
from orchestrator.match_config import MatchConfig
from orchestrator.pricing import PricingError, PricingProfile, derive_pricing_profiles
from orchestrator.spend_ledger import SpendLimits

_REPO_ROOT = Path(__file__).resolve().parent.parent
type AgentLoader = Callable[[Path], AgentConfig]
type ProfileLoader = Callable[[tuple[str, ...]], Mapping[str, PricingProfile]]


def build_budget_runtime(
    config: MatchConfig,
    *,
    load_agent: AgentLoader = load_config,
    load_profiles: ProfileLoader = derive_pricing_profiles,
) -> BudgetRuntime | None:
    if config.budget_usd is None and config.per_agent_budget_usd is None:
        return None
    models: list[str] = []
    policies: dict[int, SlotBudgetPolicy] = {}
    for entry in config.agents:
        agent = load_agent(_REPO_ROOT / entry.config)
        if agent.fallbacks:
            raise ConfigError(
                "capped matches do not support fallback models",
                path=entry.config,
                field="fallbacks",
            )
        if agent.model not in models:
            models.append(agent.model)
        policies[entry.slot] = SlotBudgetPolicy(
            model=agent.model,
            fallback_models=(),
            max_output_tokens=agent.max_tokens,
        )
    try:
        profiles = load_profiles(tuple(models))
    except PricingError as exc:
        raise ConfigError(str(exc), path="<litellm-metadata>", field="model") from exc
    return BudgetRuntime(
        limits=SpendLimits(
            budget_usd=config.budget_usd,
            per_agent_budget_usd=config.per_agent_budget_usd,
        ),
        profiles=profiles,
        slots=tuple(range(config.n_agents)),
        policies=policies,
    )
