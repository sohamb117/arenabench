from typing import Literal, cast

import pytest

from common.errors import ParseError
from harness.parser import parse, parse_json

EXPECTED_CMD_DURATION = 1.5


def test_parse_json_happy_path() -> None:
    raw = """Some prefix text.
```json
{
  "analysis": "Looking good",
  "plan": "Will do X",
  "commands": [
    {"keystrokes": "ls -l", "duration_sec": 1.5, "is_blocking": true}
  ],
  "task_complete": false
}
```
Some suffix text.
"""
    result = parse_json(raw)
    assert result.analysis == "Looking good"
    assert result.plan == "Will do X"
    assert len(result.commands) == 1
    assert result.commands[0].keystrokes == "ls -l"
    assert result.commands[0].duration_sec == EXPECTED_CMD_DURATION
    assert result.commands[0].is_blocking is True
    assert result.task_complete is False


def test_parse_json_empty_commands_and_complete() -> None:
    raw = """
    {
      "analysis": "Done",
      "plan": "Nothing",
      "commands": [],
      "task_complete": true
    }
    """
    result = parse_json(raw)
    assert result.analysis == "Done"
    assert result.plan == "Nothing"
    assert len(result.commands) == 0
    assert result.task_complete is True


def test_parse_json_malformed() -> None:
    raw = """{ "analysis": "bad", "plan": "unclosed", "commands": [] """
    with pytest.raises(ParseError) as exc:
        parse_json(raw)
    assert exc.value.parser == "json"
    assert "Unbalanced braces" in str(exc.value)


def test_parse_json_missing_field() -> None:
    raw = """{ "analysis": "Missing plan", "commands": [], "task_complete": false }"""
    with pytest.raises(ParseError) as exc:
        parse_json(raw)
    assert exc.value.parser == "json"
    assert "Validation failed" in str(exc.value)


def test_parse_json_no_json() -> None:
    raw = "Just some text with no braces."
    with pytest.raises(ParseError) as exc:
        parse_json(raw)
    assert exc.value.parser == "json"
    assert "No JSON object found" in str(exc.value)


def test_parse_invalid_json_syntax() -> None:
    raw = """{ "analysis": "bad", "plan": "bad", "commands": [], "task_complete": false, }"""
    with pytest.raises(ParseError) as exc:
        parse_json(raw)
    assert exc.value.parser == "json"
    assert "Invalid JSON" in str(exc.value)


def test_parse_dispatch() -> None:
    raw = """{ "analysis": "A", "plan": "P", "commands": [], "task_complete": false }"""
    result = parse(raw, "json")
    assert result.analysis == "A"

    bad_mode = cast(Literal["json", "xml"], "yaml")
    with pytest.raises(ParseError):
        parse(raw, bad_mode)
