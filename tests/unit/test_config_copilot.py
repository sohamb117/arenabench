import json
import pathlib

from harness.config import load_config, resolve_api_key, uses_github_copilot_auth


def _copilot_payload() -> dict[str, object]:
    return {
        "model": "github_copilot/gpt-4",
        "temperature": 0.7,
        "max_tokens": 4096,
        "request_timeout_s": 60,
        "num_retries": 3,
        "fallbacks": None,
        "reasoning_effort": None,
        "parser": "json",
        "system_prompt_path": "configs/prompts/adversarial.txt",
    }


def _write_config(tmp_path: pathlib.Path, payload: dict[str, object]) -> pathlib.Path:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    return config_path


def test_copilot_config_without_api_key_env_loads(tmp_path: pathlib.Path) -> None:
    # Given
    config_path = _write_config(tmp_path, _copilot_payload())

    # When
    cfg = load_config(config_path)

    # Then
    assert cfg.api_key_env is None
    assert uses_github_copilot_auth(cfg.model)


def test_resolve_api_key_for_copilot_returns_none(tmp_path: pathlib.Path) -> None:
    # Given
    cfg = load_config(_write_config(tmp_path, _copilot_payload()))

    # When
    result = resolve_api_key(cfg)

    # Then
    assert result is None
