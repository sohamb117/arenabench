"""Per-agent configuration schema and helpers."""

import os
import pathlib
from typing import Annotated, Literal

import pydantic

from common.errors import ConfigError

_ENV_KEY_PATTERN = r"^[A-Z][A-Z0-9_]*$"


class AgentConfig(pydantic.BaseModel):
    """Validated per-agent runtime configuration."""

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    model: Annotated[str, pydantic.Field(min_length=1)]
    temperature: Annotated[float, pydantic.Field(ge=0.0, le=2.0)]
    max_tokens: Annotated[int, pydantic.Field(ge=1, le=200_000)]
    request_timeout_s: Annotated[int, pydantic.Field(ge=1, le=600)]
    num_retries: Annotated[int, pydantic.Field(ge=0, le=10)]
    fallbacks: list[str] | None = None
    parser: Literal["json", "xml"] = "json"
    api_key_env: Annotated[str, pydantic.Field(pattern=_ENV_KEY_PATTERN)]
    system_prompt_path: str


def load_config(path: pathlib.Path) -> AgentConfig:
    """Read and parse *path* into an :class:`AgentConfig`.

    Raises:
        ConfigError: on I/O failure or any field-level validation error.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(str(exc), path=str(path)) from exc

    try:
        return AgentConfig.model_validate_json(raw)
    except pydantic.ValidationError as exc:
        errors = exc.errors(include_url=False)
        loc = errors[0]["loc"]
        field: str | None = ".".join(str(part) for part in loc) if loc else None
        raise ConfigError(str(exc), path=str(path), field=field) from exc


def resolve_api_key(cfg: AgentConfig) -> str:
    """Return the value of the env var named by ``cfg.api_key_env``.

    Raises:
        ConfigError: if the env var is not set.
    """
    value = os.environ.get(cfg.api_key_env)
    if value is None:
        raise ConfigError(
            f"env var {cfg.api_key_env!r} is not set",
            path="<env>",
            field=cfg.api_key_env,
        )
    return value


def read_system_prompt(cfg: AgentConfig, base_dir: pathlib.Path) -> str:
    """Return the stripped text of the system prompt file.

    Resolves ``cfg.system_prompt_path`` relative to *base_dir* when not
    absolute.

    Raises:
        ConfigError: if the file is missing or contains only whitespace.
    """
    raw_path = pathlib.Path(cfg.system_prompt_path)
    resolved = raw_path if raw_path.is_absolute() else base_dir / raw_path

    try:
        content = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"system prompt missing: {resolved}",
            path=str(resolved),
            field="system_prompt_path",
        ) from exc

    stripped = content.strip()
    if not stripped:
        raise ConfigError(
            f"system prompt is empty: {resolved}",
            path=str(resolved),
            field="system_prompt_path",
        )

    return stripped
