from pathlib import Path

import pytest

from common.errors import ConfigError
from harness.config import AgentConfig
from orchestrator.agent_runtime import resolve_context_limits
from orchestrator.match_config import AgentEntry


def _agent_config(*, context: int | None = None, model: str = "provider/model") -> AgentConfig:
    return AgentConfig(
        model=model,
        temperature=0.0,
        max_output_tokens=4096,
        max_context_tokens=context,
        request_timeout_s=60,
        num_retries=0,
        api_key_env="TEST_API_KEY",
        system_prompt_path="prompt.txt",
    )


def test_context_limit_defaults_to_litellm_max_input_tokens() -> None:
    entries = [AgentEntry(slot=0, user="agent0", config="agent.json")]

    limits = resolve_context_limits(
        entries,
        load_agent=lambda _path: _agent_config(),
        load_metadata=lambda _model: {"max_input_tokens": 1_050_000},
        repo_root=Path("/repo"),
    )

    assert limits == {0: 1_050_000}


def test_explicit_context_limit_wins_without_metadata_lookup() -> None:
    entries = [AgentEntry(slot=0, user="agent0", config="agent.json")]

    limits = resolve_context_limits(
        entries,
        load_agent=lambda _path: _agent_config(context=64_000),
        load_metadata=lambda _model: pytest.fail("metadata should not be loaded"),
        repo_root=Path("/repo"),
    )

    assert limits == {0: 64_000}


def test_copilot_context_metadata_uses_canonical_model_name() -> None:
    entries = [AgentEntry(slot=0, user="agent0", config="agent.json")]
    loaded_models: list[str] = []

    def load_metadata(model: str) -> dict[str, object]:
        loaded_models.append(model)
        return {"max_input_tokens": 1_050_000}

    limits = resolve_context_limits(
        entries,
        load_agent=lambda _path: _agent_config(model="github_copilot/gpt-5.5"),
        load_metadata=load_metadata,
        repo_root=Path("/repo"),
    )

    assert limits == {0: 1_050_000}
    assert loaded_models == ["gpt-5.5"]


@pytest.mark.parametrize("metadata", [{}, {"max_input_tokens": None}, {"max_input_tokens": 0}])
def test_missing_or_invalid_context_metadata_fails_closed(metadata: dict[str, object]) -> None:
    entries = [AgentEntry(slot=0, user="agent0", config="agent.json")]

    with pytest.raises(ConfigError, match="max_input_tokens"):
        resolve_context_limits(
            entries,
            load_agent=lambda _path: _agent_config(),
            load_metadata=lambda _model: metadata,
            repo_root=Path("/repo"),
        )
