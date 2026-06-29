"""End-to-end §14 scenario tests — gated behind ARENABENCH_E2E=1.

Per plan §9, each scenario has a BINARY OBSERVABLE. These tests use FAKE
LLM agents (configs/agents/fake-*.json with mock_response / mock_raise_on_turn
set) so outcomes are deterministic across runs, mirroring the plan §9
"fake LLM" intent for S15 and S17. Real-LLM e2e for S1 (demo-1v1 victory)
+ S5 (log completeness) + S6 (N=4) lives in test_match_real_vm.py.

Each test invokes the production CLI (`uv run arenabench run`) and asserts
the observable from the resulting summary.json / per-agent logs.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_IMAGE_PATH = REPO_ROOT / "vm" / "images" / "arenabench-golden-aarch64.qcow2"
DEMO_MATCH_1V1 = REPO_ROOT / "configs" / "matches" / "demo-1v1.json"
S3_MUTUAL_MATCH = REPO_ROOT / "configs" / "matches" / "s3-mutual.json"
S11_SELF_KILL_MATCH = REPO_ROOT / "configs" / "matches" / "s11-self-kill.json"
S15_WALKOVER_MATCH = REPO_ROOT / "configs" / "matches" / "s15-walkover.json"
S17_TIMEOUT_MATCH = REPO_ROOT / "configs" / "matches" / "s17-timeout.json"
S17_DURATION_MIN = 10.0
S17_DURATION_MAX = 12.0


def _skip_if_no_e2e(*, require_llm_keys: bool = False) -> None:
    if not os.environ.get("ARENABENCH_E2E"):
        pytest.skip("set ARENABENCH_E2E=1 to run real-VM e2e tests")
    if not GOLDEN_IMAGE_PATH.exists():
        pytest.skip(f"golden image not built; run vm/golden/build.sh ({GOLDEN_IMAGE_PATH} missing)")
    for tool in ("qemu-system-aarch64", "qemu-img", "cloud-localds", "ssh"):
        if not shutil.which(tool):
            pytest.skip(f"required host tool '{tool}' not on PATH")
    if require_llm_keys:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            pytest.skip("set ANTHROPIC_API_KEY")
        if not os.environ.get("OPENAI_API_KEY"):
            pytest.skip("set OPENAI_API_KEY")


def _run_match(match_config: Path, *, log_root: Path, match_id: str) -> Path:
    result = subprocess.run(
        [
            "uv",
            "run",
            "arenabench",
            "run",
            str(match_config),
            "--log-root",
            str(log_root),
            "--golden-image",
            str(GOLDEN_IMAGE_PATH),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"arenabench run exited {result.returncode}\nstdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return log_root / "matches" / match_id / "summary.json"


def _read_summary(summary_path: Path) -> dict[str, object]:
    raw = summary_path.read_text(encoding="utf-8")
    return cast(dict[str, object], json.loads(raw))


@pytest.mark.e2e
def test_s3_mutual_destruction_produces_draw(tmp_path: Path) -> None:
    """S3 + §14.3: both agents `kill -9 $$` within grace_period_s → result=='draw'.

    Deterministic via configs/matches/s3-mutual.json (both slots use
    fake-suicide agents that mock_response with a `kill -9 $$` command).
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary = _read_summary(_run_match(S3_MUTUAL_MATCH, log_root=log_root, match_id="s3-mutual"))

    assert summary.get("result") == "draw", summary
    cause = summary.get("cause")
    assert isinstance(cause, str) and cause in {
        "survivor_died_in_grace",
        "mutual_destruction",
    }, cause


@pytest.mark.e2e
def test_s4_heartbeat_observable_in_api_jsonl(tmp_path: Path) -> None:
    """S4 + §14.2: `[HEARTBEAT t=...]` prefix is provable from api.jsonl.

    Uses fake-wait agents (no real LLM calls needed). The fake-wait
    mock_response is the SAME for every turn, so when a heartbeat fires,
    the orchestrator-injected user-role turn shows up in last_user_excerpt
    of the very next llm_request envelope.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    _ = _run_match(S17_TIMEOUT_MATCH, log_root=log_root, match_id="s17-timeout")
    api_jsonl = log_root / "matches" / "s17-timeout" / "agents" / "00" / "api.jsonl"
    assert api_jsonl.is_file(), f"api.jsonl missing at {api_jsonl}"
    saw_heartbeat = False
    for line in api_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        env = cast(dict[str, object], json.loads(line))
        data = cast(dict[str, object], env.get("data", {}))
        excerpt = data.get("last_user_excerpt")
        if isinstance(excerpt, str) and "[HEARTBEAT t=" in excerpt:
            saw_heartbeat = True
            break
    assert saw_heartbeat, "expected at least one llm_request with `[HEARTBEAT t=` excerpt"


@pytest.mark.e2e
def test_s11_self_kill_produces_vsock_disconnect_plus_kill0_dead(tmp_path: Path) -> None:
    """S11 + §9 binary observable: agent issues `kill -9 $$` → harness_dead.cause
    is `vsock_disconnect+kill0_dead` (orchestrator/liveness.py:62-77).

    Deterministic via configs/matches/s11-self-kill.json (slot 0 fake-suicide,
    slot 1 fake-wait); the wait-side wins.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary = _read_summary(
        _run_match(S11_SELF_KILL_MATCH, log_root=log_root, match_id="s11-self-kill")
    )

    assert summary.get("result") == "victory", summary
    assert summary.get("winner") == 1, summary
    match_jsonl = log_root / "matches" / "s11-self-kill" / "match.jsonl"
    causes: list[str] = []
    for line in match_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        env = cast(dict[str, object], json.loads(line))
        if env.get("kind") != "harness_dead":
            continue
        data = cast(dict[str, object], env.get("data", {}))
        cause = data.get("cause")
        if isinstance(cause, str):
            causes.append(cause)
    assert any("vsock_disconnect" in c and "kill0_dead" in c for c in causes), causes


@pytest.mark.e2e
def test_s15_walkover_after_opponent_crash(tmp_path: Path) -> None:
    """S15 + §14.8: agent0 fake-crash-turn3 raises → agent1 wins with cause=='opponent_crashed'.

    Deterministic via configs/matches/s15-walkover.json. agent0 fake-crash-turn3
    raises RuntimeError on turn 3; run_harness catches → emits
    harness_exit{reason: "crash", code: 1}. Winner module emits
    cause="opponent_crashed" since last_alive_count was 2.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary = _read_summary(
        _run_match(S15_WALKOVER_MATCH, log_root=log_root, match_id="s15-walkover")
    )

    assert summary.get("result") == "victory", summary
    assert summary.get("winner") == 1, summary
    assert summary.get("cause") == "opponent_crashed", summary
    crash_events = log_root / "matches" / "s15-walkover" / "agents" / "00" / "events.jsonl"
    assert crash_events.is_file()
    saw_crash = False
    for line in crash_events.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        env = cast(dict[str, object], json.loads(line))
        if env.get("kind") != "harness_exit":
            continue
        data = cast(dict[str, object], env.get("data", {}))
        if data.get("reason") == "crash" and data.get("code") == 1:
            saw_crash = True
            break
    assert saw_crash, "expected harness_exit{reason:'crash', code:1} in agent0 events.jsonl"


@pytest.mark.e2e
def test_s17_max_duration_timeout(tmp_path: Path) -> None:
    """S17 + §14.9: max_duration_s elapses → result=='timeout',
    alive_at_timeout==[0,1], total_duration_s ∈ [10, 12].

    Deterministic via configs/matches/s17-timeout.json (heartbeat_interval_s=2,
    max_duration_s=10, both fake-wait agents). Plan §9 exact spec.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary = _read_summary(
        _run_match(S17_TIMEOUT_MATCH, log_root=log_root, match_id="s17-timeout")
    )

    assert summary.get("result") == "timeout", summary
    assert summary.get("cause") == "max_duration_exceeded", summary
    assert summary.get("winner") is None, summary
    assert summary.get("alive_at_timeout") == [0, 1], summary
    duration = summary.get("total_duration_s")
    assert isinstance(duration, (int, float))
    assert S17_DURATION_MIN <= float(duration) <= S17_DURATION_MAX, duration
