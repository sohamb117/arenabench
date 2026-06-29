from __future__ import annotations

from pathlib import Path
from typing import Literal

import pydantic

from common.errors import ConfigError
from common.ids import make_match_id


class AgentEntry(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    slot: int = pydantic.Field(ge=0, lt=16)
    user: str = pydantic.Field(pattern=r"^agent[0-9]+$")
    config: str


class CgroupLimits(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    cpu_quota_ms_per_s: int = pydantic.Field(ge=1, le=10_000)
    mem_mb: int = pydantic.Field(ge=16, le=262_144)


class MatchConfig(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(extra="forbid", frozen=True)

    match_id: str
    n_agents: int = pydantic.Field(ge=2, le=16)
    heartbeat_interval_s: int = pydantic.Field(ge=1, le=3600)
    grace_period_s: int = pydantic.Field(ge=1, le=600)
    max_duration_s: int = pydantic.Field(ge=10, le=86400)
    archive_grace_s: int = pydantic.Field(default=60, ge=0, le=3600)
    network_policy: Literal["allowlist", "full"] = "allowlist"
    cgroup_limits: CgroupLimits | None = None
    agents: list[AgentEntry]

    @pydantic.field_validator("match_id")
    @classmethod
    def _validate_match_id(cls, value: str) -> str:
        try:
            return str(make_match_id(value))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @pydantic.field_validator("agents")
    @classmethod
    def _validate_agents_non_empty(cls, value: list[AgentEntry]) -> list[AgentEntry]:
        if not value:
            raise ValueError("agents must not be empty")
        return value

    @pydantic.model_validator(mode="after")
    def _validate_agent_collection(self) -> MatchConfig:
        if len(self.agents) != self.n_agents:
            raise ConfigError(
                "invalid match config",
                path="<memory>",
                field="agents",
            )

        slots = [agent.slot for agent in self.agents]
        users = [agent.user for agent in self.agents]
        if len(set(slots)) != len(slots):
            raise ConfigError("invalid match config", path="<memory>", field="agents")
        if len(set(users)) != len(users):
            raise ConfigError("invalid match config", path="<memory>", field="agents")

        expected_slots = set(range(self.n_agents))
        if set(slots) != expected_slots:
            raise ConfigError("invalid match config", path="<memory>", field="agents")

        return self


def load_match_config(path: Path) -> MatchConfig:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(str(exc), path=str(path)) from exc

    try:
        return MatchConfig.model_validate_json(raw)
    except ConfigError:
        raise
    except pydantic.ValidationError as exc:
        errors = exc.errors(include_url=False)
        field = (
            ".".join(str(part) for part in errors[0]["loc"])
            if errors and errors[0]["loc"]
            else None
        )
        raise ConfigError(str(exc), path=str(path), field=field) from exc
