"""Per-agent configuration schema and helpers."""

import os
import pathlib
from typing import Annotated, Literal

import pydantic
from pydantic import AliasChoices

from common.errors import ConfigError

_ENV_KEY_PATTERN = r"^[A-Z][A-Z0-9_]*$"
_ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "default"]


class AgentConfig(pydantic.BaseModel):
    """Validated per-agent runtime configuration."""

    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    model: Annotated[str, pydantic.Field(min_length=1)]
    temperature: Annotated[float, pydantic.Field(ge=0.0, le=2.0)]
    max_output_tokens: int | None = pydantic.Field(
        default=None,
        ge=1,
        le=200_000,
        validation_alias=AliasChoices("max_output_tokens", "max_tokens"),
    )
    max_context_tokens: Annotated[int, pydantic.Field(ge=1, le=10_000_000)] | None = None
    request_timeout_s: Annotated[int, pydantic.Field(ge=1, le=600)]
    num_retries: Annotated[int, pydantic.Field(ge=0, le=10)]
    fallbacks: list[str] | None = None
    reasoning_effort: _ReasoningEffort | None = None
    api_mode: Literal["chat_completions", "responses"] = "chat_completions"
    parser: Literal["json", "xml"] = "json"
    api_key_env: Annotated[str, pydantic.Field(pattern=_ENV_KEY_PATTERN)] | None = None
    system_prompt_path: str
    mock_response: str | None = None
    mock_raise_on_turn: int | None = None

    @property
    def resolved_context_tokens(self) -> int:
        if self.max_context_tokens is None:
            raise ConfigError(
                "max_context_tokens must be resolved before harness startup",
                path="<config>",
                field="max_context_tokens",
            )
        return self.max_context_tokens

    @property
    def resolved_budget_output_tokens(self) -> int:
        if self.max_output_tokens is not None:
            return self.max_output_tokens
        raise ConfigError(
            "max_output_tokens must be resolved from provider metadata before harness startup",
            path="<config>",
            field="max_output_tokens",
        )

    @pydantic.model_validator(mode="after")
    def _validate_auth_mode(self) -> "AgentConfig":
        if (
            self.api_key_env is None
            and self.mock_response is None
            and self.mock_raise_on_turn is None
            and not uses_github_copilot_auth(self.model)
        ):
            raise ValueError("api_key_env required unless using mock or GitHub Copilot auth")
        return self


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


def uses_github_copilot_auth(model: str) -> bool:
    return model.startswith("github_copilot/")


def resolve_api_key(cfg: AgentConfig) -> str | None:
    """Return the value of the env var named by ``cfg.api_key_env``.

    Returns the literal string "mock" without consulting env when
    ``cfg.mock_response`` or ``cfg.mock_raise_on_turn`` is set, so fake-LLM
    e2e tests do not require real API credentials.

    Raises:
        ConfigError: if the env var is not set AND mocking is off.
    """
    if cfg.mock_response is not None or cfg.mock_raise_on_turn is not None:
        return "mock"
    if uses_github_copilot_auth(cfg.model):
        return None
    if cfg.api_key_env is None:
        raise ConfigError(
            "api_key_env missing from agent config",
            path="<config>",
            field="api_key_env",
        )
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
