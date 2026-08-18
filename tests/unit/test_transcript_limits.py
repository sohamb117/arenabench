from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType

import pytest
from pydantic import TypeAdapter
from typer.testing import CliRunner

from common.protocol import MAX_FRAME_BYTES
from orchestrator.cli import app
from orchestrator.transcript import TranscriptError, read_transcript
from orchestrator.transcript_jsonl import MAX_TRANSCRIPT_FRAMES, read_jsonl
from tests.unit.transcript_fixtures import JsonRecord, envelope, exact_run, write_jsonl

_JSON_RECORD_ADAPTER = TypeAdapter[JsonRecord](JsonRecord)
_MAX_CLI_ERROR_CHARS = 512


class _CountingFile:
    def __init__(self, line: bytes) -> None:
        self._line = line
        self.sizes: list[int] = []

    def __enter__(self) -> _CountingFile:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def readline(self, size: int = -1) -> bytes:
        self.sizes.append(size)
        line, self._line = self._line, b""
        return line


def test_reader_uses_bounded_readline_before_allocating(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _CountingFile(b"x" * (MAX_FRAME_BYTES + 1))

    def exists(_path: Path) -> bool:
        return True

    def is_symlink(_path: Path) -> bool:
        return False

    def is_file(_path: Path) -> bool:
        return True

    def open_file(_path: Path, _mode: str) -> _CountingFile:
        return fake

    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "open", open_file)

    with pytest.raises(TranscriptError, match="oversized JSONL line"):
        tuple(read_jsonl(Path("counting.jsonl")))

    assert fake.sizes == [MAX_FRAME_BYTES + 1]


def test_reader_caps_total_frames(tmp_path: Path) -> None:
    run = exact_run(tmp_path)
    records = [
        envelope(
            "2026-08-17T12:00:01Z",
            "agent0",
            "heartbeat_injected",
            {"turn": index, "elapsed_s": 0.0, "payload": "x"},
        )
        for index in range(MAX_TRANSCRIPT_FRAMES + 1)
    ]
    write_jsonl(run / "agents" / "00" / "events.jsonl", records)

    with pytest.raises(TranscriptError, match="frame count exceeds"):
        read_transcript(run)


def test_reader_caps_agent_log_file_count(tmp_path: Path) -> None:
    run = exact_run(tmp_path)
    agents = run / "agents"
    for slot in range(1, 17):
        (agents / f"{slot:02d}").mkdir()

    with pytest.raises(TranscriptError, match="agent log directory count exceeds 16"):
        read_transcript(run)


def test_reader_rejects_unknown_wire_fields(tmp_path: Path) -> None:
    run = exact_run(tmp_path)
    api_path = run / "agents" / "00" / "api.jsonl"
    records = [
        _JSON_RECORD_ADAPTER.validate_json(line)
        for line in api_path.read_text(encoding="utf-8").splitlines()
    ]
    data = records[0]["data"]
    assert isinstance(data, dict)
    data["unexpected"] = "silently dropped"
    write_jsonl(api_path, records)

    with pytest.raises(TranscriptError, match="extra_forbidden"):
        read_transcript(run)


@pytest.mark.parametrize("match_id", ["../escape", "bad\x1b]0;title\x07"])
def test_manifest_rejects_invalid_match_id(tmp_path: Path, match_id: str) -> None:
    run = exact_run(tmp_path)
    manifest_path = run / "run.json"
    manifest = _JSON_RECORD_ADAPTER.validate_json(manifest_path.read_text(encoding="utf-8"))
    manifest["match_id"] = match_id
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TranscriptError, match="invalid run manifest"):
        read_transcript(run)


def test_manifest_rejects_invalid_run_id(tmp_path: Path) -> None:
    run = exact_run(tmp_path)
    manifest_path = run / "run.json"
    manifest = _JSON_RECORD_ADAPTER.validate_json(manifest_path.read_text(encoding="utf-8"))
    manifest["run_id"] = "bad-run-id"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TranscriptError, match="invalid run manifest"):
        read_transcript(run)


def test_reader_accepts_pretty_printed_multiline_manifest(tmp_path: Path) -> None:
    run = exact_run(tmp_path)
    manifest_path = run / "run.json"
    manifest = _JSON_RECORD_ADAPTER.validate_json(manifest_path.read_text(encoding="utf-8"))
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    document = read_transcript(run)

    assert document.run.match_id == "exact"


def test_cli_malformed_error_does_not_reflect_sensitive_input(tmp_path: Path) -> None:
    run = exact_run(tmp_path)
    secret = "TOP_SECRET_123\x1b]0;owned\x07"
    api_path = run / "agents" / "00" / "api.jsonl"
    record = _JSON_RECORD_ADAPTER.validate_json(
        api_path.read_text(encoding="utf-8").splitlines()[0]
    )
    data = record["data"]
    assert isinstance(data, dict)
    data["turn"] = secret
    write_jsonl(api_path, [record])

    result = CliRunner().invoke(app, ["transcript", str(run)])

    error = result.output + (result.stderr or "")
    assert result.exit_code == 1
    assert "TOP_SECRET_123" not in error
    assert "owned" not in error
    assert all(character in "\n\t" or character.isprintable() for character in error)
    assert len(error.removeprefix("ERROR ").strip()) <= _MAX_CLI_ERROR_CHARS


def test_invalid_reservation_destination_is_ignored(tmp_path: Path) -> None:
    run = exact_run(tmp_path)
    match_path = run / "match.jsonl"
    record = _JSON_RECORD_ADAPTER.validate_json(
        match_path.read_text(encoding="utf-8").splitlines()[0]
    )
    record["dst"] = "agent0\nRUN forged"
    write_jsonl(match_path, [record])

    document = read_transcript(run)

    assert document.agents[0].turns[0].attempts[0].reservation is None
