from __future__ import annotations

from pathlib import Path

import pytest

from common.errors import ConfigError
from harness.config import AgentConfig
from orchestrator.budget_preflight import build_budget_runtime
from orchestrator.match_config import AgentEntry, MatchConfig
from orchestrator.pricing import PricingError, PricingProfile


def _config(*, capped: bool) -> MatchConfig:
    return MatchConfig(
        match_id="preflight",
        n_agents=2,
        heartbeat_interval_s=10,
        grace_period_s=10,
        max_duration_s=100,
        budget_usd=1.0 if capped else None,
        agents=[
            AgentEntry(slot=0, user="agent0", config="a0.json"),
            AgentEntry(slot=1, user="agent1", config="a1.json"),
        ],
    )


def _agent(model: str, fallbacks: list[str] | None = None) -> AgentConfig:
    return AgentConfig(
        model=model,
        temperature=0.0,
        max_output_tokens=100,
        request_timeout_s=10,
        num_retries=1,
        fallbacks=fallbacks,
        api_key_env="TEST_API_KEY",
        system_prompt_path="prompt.txt",
    )


def test_uncapped_preflight_does_not_load_agents_or_metadata() -> None:
    def load_agent(_path: Path) -> AgentConfig:
        raise AssertionError("uncapped preflight must not load agent config")

    def load_profiles(_models: tuple[str, ...]) -> dict[str, PricingProfile]:
        raise AssertionError("uncapped preflight must not load pricing")

    runtime = build_budget_runtime(
        _config(capped=False), load_agent=load_agent, load_profiles=load_profiles
    )

    assert runtime is None


def test_capped_preflight_loads_every_primary_once() -> None:
    agents = {
        "a0.json": _agent("provider/primary-a"),
        "a1.json": _agent("provider/primary-b"),
    }
    loaded_models: list[tuple[str, ...]] = []

    def load_agent(path: Path) -> AgentConfig:
        return agents[path.name]

    def load_profiles(models: tuple[str, ...]) -> dict[str, PricingProfile]:
        loaded_models.append(models)
        return {
            model: PricingProfile(model=model, input_usd_per_token=0.1, output_usd_per_token=0.2)
            for model in models
        }

    runtime = build_budget_runtime(
        _config(capped=True), load_agent=load_agent, load_profiles=load_profiles
    )

    assert runtime is not None
    assert loaded_models == [("provider/primary-a", "provider/primary-b")]


def test_capped_preflight_rejects_nonempty_fallbacks() -> None:
    agents = {
        "a0.json": _agent("provider/primary-a", ["provider/fallback"]),
        "a1.json": _agent("provider/primary-b"),
    }

    def load_agent(path: Path) -> AgentConfig:
        return agents[path.name]

    with pytest.raises(ConfigError, match="fallback"):
        build_budget_runtime(_config(capped=True), load_agent=load_agent)


def test_capped_preflight_reports_metadata_failure_as_config_error() -> None:
    agents = {
        "a0.json": _agent("provider/primary-a"),
        "a1.json": _agent("provider/primary-b"),
    }

    def load_agent(path: Path) -> AgentConfig:
        return agents[path.name]

    def fail_profiles(_models: tuple[str, ...]) -> dict[str, PricingProfile]:
        raise PricingError(model="provider/primary-a", reason="missing token pricing")

    with pytest.raises(ConfigError, match="missing token pricing"):
        build_budget_runtime(
            _config(capped=True),
            load_agent=load_agent,
            load_profiles=fail_profiles,
        )
