from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from common.errors import ConfigError
from orchestrator.cloudinit import SecretFile

COPILOT_MODEL_PREFIX = "github_copilot/"
_ACCESS_TOKEN_FILE = "access-token"
_API_KEY_FILE = "api-key.json"
_GUEST_BASE = ".config/litellm/github_copilot"


@dataclass(frozen=True, slots=True)
class AgentCredentials:
    env_vars: dict[int, tuple[tuple[str, str], ...]]
    secret_files: dict[int, tuple[SecretFile, ...]]


def resolve_copilot_secret_files(agent_cfg_path: Path) -> tuple[SecretFile, ...]:
    token_dir = _token_dir()
    access_name = os.environ.get("GITHUB_COPILOT_ACCESS_TOKEN_FILE", _ACCESS_TOKEN_FILE)
    api_key_name = os.environ.get("GITHUB_COPILOT_API_KEY_FILE", _API_KEY_FILE)
    access_token = _read_required_secret(
        token_dir / access_name,
        f"{_GUEST_BASE}/{_ACCESS_TOKEN_FILE}",
        agent_cfg_path,
    )
    api_key = _read_optional_secret(
        token_dir / api_key_name,
        f"{_GUEST_BASE}/{_API_KEY_FILE}",
    )
    if api_key is None:
        return (access_token,)
    return (access_token, api_key)


def _read_optional_secret(host_path: Path, guest_path: str) -> SecretFile | None:
    try:
        content = host_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return SecretFile(path=guest_path, content=content)


def _token_dir() -> Path:
    configured = os.environ.get("GITHUB_COPILOT_TOKEN_DIR")
    if configured is not None:
        return Path(configured).expanduser()
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config is not None:
        return Path(xdg_config).expanduser() / "litellm" / "github_copilot"
    return Path.home() / ".config" / "litellm" / "github_copilot"


def _read_required_secret(host_path: Path, guest_path: str, agent_cfg_path: Path) -> SecretFile:
    try:
        content = host_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(
            f"GitHub Copilot token file missing: {host_path}",
            path=str(agent_cfg_path),
            field=f"github_copilot.{host_path.name}",
        ) from exc
    return SecretFile(path=guest_path, content=content)
