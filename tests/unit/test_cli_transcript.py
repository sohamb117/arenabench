from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from typer.testing import CliRunner

from orchestrator.cli import app
from tests.unit.transcript_fixtures import exact_run


def test_cli_transcript_text_has_stable_semantic_sections(tmp_path: Path) -> None:
    run = exact_run(tmp_path)

    result = CliRunner().invoke(app, ["transcript", str(run), "--agent", "0", "--turn", "2"])

    assert result.exit_code == 0
    sections = (
        "RUN exact",
        "AGENT 0",
        "TURN 2",
        "ATTEMPT 0",
        "CONTEXT",
        "REPLY",
        "PARSE",
        "COMMAND",
        "RESULT",
        "USAGE",
        "LATENCY",
        "COST",
    )
    for section in sections:
        assert section in result.stdout
    assert "\x1b[" not in result.stdout


def test_cli_transcript_json_emits_versioned_document(tmp_path: Path) -> None:
    run = exact_run(tmp_path)

    result = CliRunner().invoke(app, ["transcript", str(run), "--format", "json"])

    assert result.exit_code == 0
    payload = cast(dict[str, object], json.loads(result.stdout))
    assert payload["schema_version"] == 1
    assert '"slot": 0' in result.stdout


def test_cli_transcript_errors_exit_one(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["transcript", str(tmp_path / "missing")])

    assert result.exit_code == 1
    assert "ERROR" in (result.output + (result.stderr or ""))


def test_cli_transcript_invalid_filters_exit_one(tmp_path: Path) -> None:
    run = exact_run(tmp_path)

    for args in (("--agent", "-1"), ("--turn", "-1"), ("--format", "yaml")):
        result = CliRunner().invoke(app, ["transcript", str(run), *args])
        assert result.exit_code == 1
        assert "ERROR" in (result.output + (result.stderr or ""))
