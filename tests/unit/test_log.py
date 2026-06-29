from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter
from pytest import CaptureFixture

from common.log import configure, get_logger, jsonl_sink

_EXPECTED_LINE_COUNT = 2


def _parse_line(line: str) -> dict[str, object]:
    return TypeAdapter(dict[str, object]).validate_json(line)


def _parse_timestamp(value: object) -> datetime:
    assert isinstance(value, str)
    assert value.endswith("Z")
    assert "." in value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def test_configure_emits_json_to_stderr(capsys: CaptureFixture[str]) -> None:
    configure()

    get_logger("x").info("hello", foo=1)

    captured = capsys.readouterr()
    line = captured.err.strip()
    payload = _parse_line(line)

    assert payload["event"] == "hello"
    assert payload["logger"] == "x"
    assert payload["level"] == "info"
    assert payload["foo"] == 1
    _parse_timestamp(payload["ts"])


def test_jsonl_sink_writes_ordered_lines_and_required_keys(
    tmp_path: Path,
    capsys: CaptureFixture[str],
) -> None:
    configure()

    path = tmp_path / "out.jsonl"
    with jsonl_sink(path):
        logger = get_logger("x")
        logger.info("first", foo=1)
        logger.info("second", foo=1)

    captured = capsys.readouterr()
    err_lines = [line for line in captured.err.splitlines() if line]
    assert len(err_lines) == _EXPECTED_LINE_COUNT

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == _EXPECTED_LINE_COUNT

    first = _parse_line(lines[0])
    second = _parse_line(lines[1])

    assert first["event"] == "first"
    assert second["event"] == "second"
    assert first["foo"] == 1
    assert second["foo"] == 1
    assert first["logger"] == "x"
    assert second["logger"] == "x"
    assert first["level"] == "info"
    assert second["level"] == "info"
    for payload in (first, second):
        for key in ("ts", "level", "logger", "event"):
            assert key in payload
        _parse_timestamp(payload["ts"])


def test_jsonl_timestamp_has_utc_microseconds_and_z_suffix(capsys: CaptureFixture[str]) -> None:
    configure()

    get_logger("x").info("hello")

    payload = _parse_line(capsys.readouterr().err.strip())
    parsed = _parse_timestamp(payload["ts"])

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None
