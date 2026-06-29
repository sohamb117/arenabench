# Schema and parsing strategy adapted from terminal-bench Terminus 2
# (https://github.com/laude-institute/terminal-bench @ 1a6ffa9, Apache-2.0).
# Reimplemented for arenabench's protocol; no upstream code copied verbatim.

import json
import re
import xml.etree.ElementTree as ET
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

from common.errors import ParseError
from common.protocol import BashCommand


class ParsedResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    analysis: str
    plan: str
    commands: list[BashCommand]
    task_complete: bool


def parse_json(raw: str) -> ParsedResponse:
    match = re.search(r"(?:^|\s)\{", raw)
    if not match:
        raise ParseError("No JSON object found", parser="json")

    start_idx = raw.find("{", match.start())
    open_braces = 0
    end_idx = -1

    for i in range(start_idx, len(raw)):
        if raw[i] == "{":
            open_braces += 1
        elif raw[i] == "}":
            open_braces -= 1
            if open_braces == 0:
                end_idx = i
                break

    if end_idx == -1:
        raise ParseError("Unbalanced braces in JSON", parser="json")

    json_str = raw[start_idx : end_idx + 1]

    try:
        data: object = cast(object, json.loads(json_str))
    except json.JSONDecodeError as e:
        raise ParseError(
            f"Invalid JSON: {e.msg}",
            parser="json",
            line=e.lineno,
            column=e.colno,
        ) from e

    try:
        return ParsedResponse.model_validate(data)
    except Exception as e:
        raise ParseError(f"Validation failed: {e}", parser="json") from e


def parse_xml(raw: str) -> ParsedResponse:
    start_idx = raw.find("<response>")
    end_idx = raw.rfind("</response>")

    if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
        raise ParseError("No <response> tags found", parser="xml")

    xml_str = raw[start_idx : end_idx + len("</response>")]

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as e:
        line, column = e.position
        raise ParseError(f"Invalid XML: {e}", parser="xml", line=line, column=column) from e

    try:
        analysis_elem = root.find("analysis")
        plan_elem = root.find("plan")
        task_complete_elem = root.find("task_complete")

        data: dict[str, object] = {}
        if analysis_elem is not None:
            data["analysis"] = analysis_elem.text or ""
        if plan_elem is not None:
            data["plan"] = plan_elem.text or ""

        commands: list[dict[str, object]] = []
        commands_elem = root.find("commands")
        if commands_elem is not None:
            for cmd_elem in commands_elem.findall("command"):
                keystrokes_elem = cmd_elem.find("keystrokes")
                duration_elem = cmd_elem.find("duration_sec")
                blocking_elem = cmd_elem.find("is_blocking")

                keystrokes = ""
                if keystrokes_elem is not None and keystrokes_elem.text:
                    keystrokes = keystrokes_elem.text

                duration_sec = 0.0
                if duration_elem is not None and duration_elem.text:
                    duration_sec = float(duration_elem.text)

                is_blocking = False
                if blocking_elem is not None and blocking_elem.text:
                    is_blocking = blocking_elem.text.strip().lower() == "true"

                commands.append(
                    {
                        "keystrokes": keystrokes,
                        "duration_sec": duration_sec,
                        "is_blocking": is_blocking,
                    }
                )

        data["commands"] = commands

        if task_complete_elem is not None:
            is_complete = False
            if task_complete_elem.text:
                is_complete = task_complete_elem.text.strip().lower() == "true"
            data["task_complete"] = is_complete

        return ParsedResponse.model_validate(data)
    except Exception as e:
        raise ParseError(f"Validation failed: {e}", parser="xml") from e


def parse(raw: str, mode: Literal["json", "xml"]) -> ParsedResponse:
    if mode == "json":
        return parse_json(raw)
    elif mode == "xml":
        return parse_xml(raw)
    else:
        raise ParseError(f"Unknown parser mode: {mode}", parser=str(mode))
