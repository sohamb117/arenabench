"""Unit tests for harness.config."""

import json
import pathlib

import pytest

from common.errors import ConfigError
from harness.config import load_config, read_system_prompt, resolve_api_key

# ── constants (ruff PLR2004 — no magic numbers in assertions) ─────────────────
EXPECTED_MODEL = "anthropic/claude-opus-4-7"
EXPECTED_TEMPERATURE = 0.7
EXPECTED_MAX_TOKENS = 4096
EXPECTED_REQUEST_TIMEOUT_S = 60
EXPECTED_NUM_RETRIES = 3
EXPECTED_REASONING_EFFORT = "medium"
EXPECTED_API_KEY_ENV = "ANTHROPIC_API_KEY"
EXPECTED_PARSER = "json"
EXPECTED_SYSTEM_PROMPT_PATH = "configs/prompts/adversarial.txt"
EXPECTED_API_KEY_VALUE = "sk-test-abc123"

TEMPERATURE_ABOVE_MAX = 2.1
MAX_TOKENS_ZERO = 0


# ── helpers ───────────────────────────────────────────────────────────────────


def _valid_payload() -> dict[str, object]:
    return {
        "model": EXPECTED_MODEL,
        "temperature": EXPECTED_TEMPERATURE,
        "max_tokens": EXPECTED_MAX_TOKENS,
        "request_timeout_s": EXPECTED_REQUEST_TIMEOUT_S,
        "num_retries": EXPECTED_NUM_RETRIES,
        "fallbacks": None,
        "reasoning_effort": EXPECTED_REASONING_EFFORT,
        "parser": EXPECTED_PARSER,
        "api_key_env": EXPECTED_API_KEY_ENV,
        "system_prompt_path": EXPECTED_SYSTEM_PROMPT_PATH,
    }


def _write_config(tmp_path: pathlib.Path, payload: dict[str, object]) -> pathlib.Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


# ── valid round-trip ──────────────────────────────────────────────────────────


def test_load_config_valid_round_trips(tmp_path: pathlib.Path) -> None:
    # Given
    p = _write_config(tmp_path, _valid_payload())

    # When
    cfg = load_config(p)

    # Then
    assert cfg.model == EXPECTED_MODEL
    assert cfg.temperature == EXPECTED_TEMPERATURE
    assert cfg.max_tokens == EXPECTED_MAX_TOKENS
    assert cfg.request_timeout_s == EXPECTED_REQUEST_TIMEOUT_S
    assert cfg.num_retries == EXPECTED_NUM_RETRIES
    assert cfg.reasoning_effort == EXPECTED_REASONING_EFFORT
    assert cfg.parser == EXPECTED_PARSER
    assert cfg.api_key_env == EXPECTED_API_KEY_ENV
    assert cfg.system_prompt_path == EXPECTED_SYSTEM_PROMPT_PATH


# ── field validation rejections ───────────────────────────────────────────────


def test_temperature_above_max_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = dict(_valid_payload())
    payload["temperature"] = TEMPERATURE_ABOVE_MAX
    p = _write_config(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_config(p)


def test_invalid_parser_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = dict(_valid_payload())
    payload["parser"] = "yaml"
    p = _write_config(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_config(p)


def test_invalid_reasoning_effort_raises_config_error(tmp_path: pathlib.Path) -> None:
    payload = dict(_valid_payload())
    payload["reasoning_effort"] = "extreme"
    p = _write_config(tmp_path, payload)

    with pytest.raises(ConfigError):
        load_config(p)


def test_lowercase_api_key_env_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = dict(_valid_payload())
    payload["api_key_env"] = "anthropic_api_key"
    p = _write_config(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_api_key_env_for_non_copilot_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = dict(_valid_payload())
    del payload["api_key_env"]
    p = _write_config(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_config(p)


def test_max_tokens_zero_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = dict(_valid_payload())
    payload["max_tokens"] = MAX_TOKENS_ZERO
    p = _write_config(tmp_path, payload)

    # When / Then
    with pytest.raises(ConfigError):
        load_config(p)


def test_missing_file_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    missing = tmp_path / "nonexistent.json"

    # When / Then
    with pytest.raises(ConfigError):
        load_config(missing)


# ── resolve_api_key ───────────────────────────────────────────────────────────


def test_resolve_api_key_when_set_returns_value(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    cfg = load_config(_write_config(tmp_path, _valid_payload()))
    monkeypatch.setenv(EXPECTED_API_KEY_ENV, EXPECTED_API_KEY_VALUE)

    # When
    result = resolve_api_key(cfg)

    # Then
    assert result == EXPECTED_API_KEY_VALUE


def test_resolve_api_key_when_unset_raises_config_error(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given
    cfg = load_config(_write_config(tmp_path, _valid_payload()))
    monkeypatch.delenv(EXPECTED_API_KEY_ENV, raising=False)

    # When / Then
    with pytest.raises(ConfigError):
        resolve_api_key(cfg)


# ── read_system_prompt ────────────────────────────────────────────────────────


def test_read_system_prompt_returns_stripped_content(tmp_path: pathlib.Path) -> None:
    # Given
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("  Stay alive.  \n", encoding="utf-8")
    payload = dict(_valid_payload())
    payload["system_prompt_path"] = "prompt.txt"
    cfg = load_config(_write_config(tmp_path, payload))

    # When
    result = read_system_prompt(cfg, base_dir=tmp_path)

    # Then
    assert result == "Stay alive."


def test_read_system_prompt_missing_raises_config_error(tmp_path: pathlib.Path) -> None:
    # Given
    payload = dict(_valid_payload())
    payload["system_prompt_path"] = "no_such_prompt.txt"
    cfg = load_config(_write_config(tmp_path, payload))

    # When / Then
    with pytest.raises(ConfigError):
        read_system_prompt(cfg, base_dir=tmp_path)


def test_read_system_prompt_whitespace_only_raises_config_error(
    tmp_path: pathlib.Path,
) -> None:
    # Given
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("   \n\t  ", encoding="utf-8")
    payload = dict(_valid_payload())
    payload["system_prompt_path"] = "empty.txt"
    cfg = load_config(_write_config(tmp_path, payload))

    # When / Then
    with pytest.raises(ConfigError):
        read_system_prompt(cfg, base_dir=tmp_path)


# ── field surfaces in ConfigError ────────────────────────────────────────────


def test_field_violation_surfaces_in_config_error_field(tmp_path: pathlib.Path) -> None:
    # Given — temperature out of range; pydantic loc → ("temperature",)
    payload = dict(_valid_payload())
    payload["temperature"] = TEMPERATURE_ABOVE_MAX
    p = _write_config(tmp_path, payload)

    # When
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)

    # Then
    assert exc_info.value.field == "temperature"
