import pytest

from common.errors import ParseError
from harness.parser import parse, parse_xml

EXPECTED_CMD_DURATION = 2.0


def test_parse_xml_happy_path() -> None:
    raw = """Prefix text
<response>
  <analysis>Looks good</analysis>
  <plan>Doing Y</plan>
  <commands>
    <command>
      <keystrokes>pwd</keystrokes>
      <duration_sec>2.0</duration_sec>
      <is_blocking>true</is_blocking>
    </command>
  </commands>
  <task_complete>false</task_complete>
</response>
Suffix text
"""
    result = parse_xml(raw)
    assert result.analysis == "Looks good"
    assert result.plan == "Doing Y"
    assert len(result.commands) == 1
    assert result.commands[0].keystrokes == "pwd"
    assert result.commands[0].duration_sec == EXPECTED_CMD_DURATION
    assert result.commands[0].is_blocking is True
    assert result.task_complete is False


def test_parse_xml_empty_commands() -> None:
    raw = """<response>
  <analysis>Done</analysis>
  <plan>Nothing more</plan>
  <commands></commands>
  <task_complete>true</task_complete>
</response>"""
    result = parse_xml(raw)
    assert result.analysis == "Done"
    assert len(result.commands) == 0
    assert result.task_complete is True


def test_parse_xml_missing_tags() -> None:
    raw = "Just prose, no response."
    with pytest.raises(ParseError) as exc:
        parse_xml(raw)
    assert exc.value.parser == "xml"
    assert "No <response> tags found" in str(exc.value)


def test_parse_xml_invalid_syntax() -> None:
    raw = """<response><analysis>Unclosed<analysis></response>"""
    with pytest.raises(ParseError) as exc:
        parse_xml(raw)
    assert exc.value.parser == "xml"
    assert "Invalid XML" in str(exc.value)


def test_parse_xml_missing_field() -> None:
    raw = """<response>
  <analysis>Missing plan</analysis>
  <commands></commands>
  <task_complete>false</task_complete>
</response>"""
    with pytest.raises(ParseError) as exc:
        parse_xml(raw)
    assert exc.value.parser == "xml"
    assert "Validation failed" in str(exc.value)


def test_parse_xml_dispatch() -> None:
    raw = """<response>
  <analysis>A</analysis>
  <plan>P</plan>
  <commands></commands>
  <task_complete>false</task_complete>
</response>"""
    result = parse(raw, "xml")
    assert result.analysis == "A"
