"""Every user-facing config under configs/ must parse and validate.

Parametrized over the on-disk configs so a shipped example (match or agent)
can never silently break for a new user — adding a new configs/ file is
automatically covered.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.config import load_config
from orchestrator.match_config import load_match_config

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MATCH_DIR = _REPO_ROOT / "configs" / "matches"
_AGENT_DIR = _REPO_ROOT / "configs" / "agents"


def _config_id(path: Path) -> str:
    return path.name


@pytest.mark.parametrize("match_path", sorted(_MATCH_DIR.glob("*.json")), ids=_config_id)
def test_shipped_match_config_validates(match_path: Path) -> None:
    cfg = load_match_config(match_path)
    assert cfg.n_agents == len(cfg.agents)
    assert [a.slot for a in cfg.agents] == list(range(cfg.n_agents))


@pytest.mark.parametrize("agent_path", sorted(_AGENT_DIR.glob("*.json")), ids=_config_id)
def test_shipped_agent_config_validates(agent_path: Path) -> None:
    cfg = load_config(agent_path)
    assert cfg.model
    prompt = _REPO_ROOT / cfg.system_prompt_path
    assert prompt.is_file(), f"{agent_path.name}: missing prompt {cfg.system_prompt_path}"
