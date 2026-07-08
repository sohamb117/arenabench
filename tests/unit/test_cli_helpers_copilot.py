"""Unit tests for GitHub Copilot credential resolution in orchestrator._cli_helpers.

Split out from test_cli_helpers.py to keep each test module within the 250-LOC cap.
Covers resolve_agent_credentials seeding Copilot token files into per-agent guest
secrets: access-token is required (missing → ConfigError), api-key.json is optional.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from common.errors import ConfigError
from orchestrator._cli_helpers import resolve_agent_credentials
from orchestrator.cloudinit import SecretFile
from orchestrator.match_config import AgentEntry


def _write_copilot_agent(
    tmp_path: Path,
    slot: int,
    *,
    system_prompt_path: str,
    model: str = "github_copilot/gpt-4",
    api_key_env: str | None = None,
) -> Path:
    agent_dir = tmp_path / "configs" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = tmp_path / system_prompt_path
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(f"prompt-for-slot-{slot}\n", encoding="utf-8")
    cfg_path = agent_dir / f"a{slot}.json"
    cfg_path.write_text(
        json.dumps(
            {
                "model": model,
                "temperature": 0.7,
                "max_tokens": 1000,
                "request_timeout_s": 60,
                "num_retries": 1,
                "parser": "json",
                "system_prompt_path": system_prompt_path,
            }
            | ({} if api_key_env is None else {"api_key_env": api_key_env})
        ),
        encoding="utf-8",
    )
    return cfg_path


def test_resolve_agent_credentials_seeds_copilot_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("orchestrator._cli_helpers._REPO_ROOT", tmp_path)
    token_dir = tmp_path / "copilot-cache"
    token_dir.mkdir()
    (token_dir / "access-token").write_text("github-access-token\n", encoding="utf-8")
    (token_dir / "api-key.json").write_text('{"token":"copilot-api-key"}', encoding="utf-8")
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(token_dir))
    _write_copilot_agent(
        tmp_path,
        0,
        system_prompt_path="configs/prompts/p.txt",
        model="github_copilot/gpt-4",
        api_key_env=None,
    )
    agents = [AgentEntry(slot=0, user="agent0", config="configs/agents/a0.json")]

    out = resolve_agent_credentials(agents)

    assert out.env_vars == {0: ()}
    assert out.secret_files == {
        0: (
            SecretFile(".config/litellm/github_copilot/access-token", "github-access-token\n"),
            SecretFile(
                ".config/litellm/github_copilot/api-key.json",
                '{"token":"copilot-api-key"}',
            ),
        )
    }


def test_resolve_agent_credentials_requires_copilot_access_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("orchestrator._cli_helpers._REPO_ROOT", tmp_path)
    token_dir = tmp_path / "empty-copilot-cache"
    token_dir.mkdir()
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(token_dir))
    _write_copilot_agent(
        tmp_path,
        0,
        system_prompt_path="configs/prompts/p.txt",
        model="github_copilot/gpt-4",
        api_key_env=None,
    )
    agents = [AgentEntry(slot=0, user="agent0", config="configs/agents/a0.json")]

    with pytest.raises(ConfigError) as exc:
        resolve_agent_credentials(agents)

    assert exc.value.field == "github_copilot.access-token"


def test_resolve_agent_credentials_allows_missing_copilot_api_key_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("orchestrator._cli_helpers._REPO_ROOT", tmp_path)
    token_dir = tmp_path / "copilot-cache"
    token_dir.mkdir()
    (token_dir / "access-token").write_text("github-access-token\n", encoding="utf-8")
    monkeypatch.setenv("GITHUB_COPILOT_TOKEN_DIR", str(token_dir))
    _write_copilot_agent(
        tmp_path,
        0,
        system_prompt_path="configs/prompts/p.txt",
        model="github_copilot/gpt-4",
        api_key_env=None,
    )
    agents = [AgentEntry(slot=0, user="agent0", config="configs/agents/a0.json")]

    out = resolve_agent_credentials(agents)

    assert out.secret_files == {
        0: (SecretFile(".config/litellm/github_copilot/access-token", "github-access-token\n"),)
    }
