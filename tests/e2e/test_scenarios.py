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
from datetime import datetime
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
S11_DEATH_WINDOW_S = 15.0


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


def _iter_envelopes(path: Path) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(cast(dict[str, object], json.loads(line)))
    return out


def _ts(env: dict[str, object]) -> datetime:
    raw = env.get("ts")
    assert isinstance(raw, str)
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


@pytest.mark.e2e
def test_s3_mutual_destruction_produces_draw(tmp_path: Path) -> None:
    """S3 + §14.3: both agents `kill -9 $$` within grace_period_s → result=='draw'."""
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
def test_s4_heartbeat_injected_precedes_llm_request_with_prefix(tmp_path: Path) -> None:
    """S4 + §14.2: heartbeat_injected envelope MUST appear in api.jsonl BEFORE
    the next llm_request whose last_user_excerpt carries the `[HEARTBEAT t=`
    prefix. Causal pair, not just substring search.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    _ = _run_match(S17_TIMEOUT_MATCH, log_root=log_root, match_id="s17-timeout")
    api_jsonl = log_root / "matches" / "s17-timeout" / "agents" / "00" / "api.jsonl"
    envs = _iter_envelopes(api_jsonl)

    saw_pair = False
    last_injected_ts: datetime | None = None
    for env in envs:
        kind = env.get("kind")
        if kind == "heartbeat_injected":
            last_injected_ts = _ts(env)
            continue
        if kind != "llm_request":
            continue
        data = cast(dict[str, object], env.get("data", {}))
        excerpt = data.get("last_user_excerpt")
        if (
            isinstance(excerpt, str)
            and "[HEARTBEAT t=" in excerpt
            and last_injected_ts is not None
            and _ts(env) >= last_injected_ts
        ):
            saw_pair = True
            break
    assert saw_pair, "expected heartbeat_injected→llm_request causal pair with [HEARTBEAT t=]"


@pytest.mark.e2e
def test_s11_self_kill_exact_cause_within_threshold(tmp_path: Path) -> None:
    """S11 + §9 binary observable: cause is EXACTLY `vsock_disconnect+kill0_dead`,
    and harness_dead arrives within silence_threshold_s + poll slack of the
    self-kill bash_request.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary = _read_summary(
        _run_match(S11_SELF_KILL_MATCH, log_root=log_root, match_id="s11-self-kill")
    )
    assert summary.get("result") == "victory", summary
    assert summary.get("winner") == 1, summary

    match_dir = log_root / "matches" / "s11-self-kill"
    dead_envs: list[dict[str, object]] = []
    for env in _iter_envelopes(match_dir / "match.jsonl"):
        if env.get("kind") == "harness_dead":
            dead_envs.append(env)
    assert len(dead_envs) == 1, dead_envs
    data = cast(dict[str, object], dead_envs[0].get("data", {}))
    assert data.get("cause") == "vsock_disconnect+kill0_dead", data

    kill_request_ts: datetime | None = None
    for env in _iter_envelopes(match_dir / "agents" / "00" / "bash.jsonl"):
        if env.get("kind") != "bash_request":
            continue
        bdata = cast(dict[str, object], env.get("data", {}))
        commands = cast(list[dict[str, object]], bdata.get("commands", []))
        for cmd in commands:
            if "kill -9" in str(cmd.get("keystrokes", "")):
                kill_request_ts = _ts(env)
                break
        if kill_request_ts is not None:
            break
    assert kill_request_ts is not None, "expected bash_request with `kill -9` in agent0"
    delta = (_ts(dead_envs[0]) - kill_request_ts).total_seconds()
    assert 0 <= delta <= S11_DEATH_WINDOW_S, (
        f"harness_dead must arrive within {S11_DEATH_WINDOW_S}s of self-kill; got {delta}s"
    )


@pytest.mark.e2e
def test_s15_walkover_after_opponent_crash(tmp_path: Path) -> None:
    """S15 + §14.8: agent0 fake-crash-turn3 raises → agent1 wins with cause=='opponent_crashed'."""
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
    saw_crash = False
    for env in _iter_envelopes(crash_events):
        if env.get("kind") != "harness_exit":
            continue
        data = cast(dict[str, object], env.get("data", {}))
        if data.get("reason") == "crash" and data.get("code") == 1:
            saw_crash = True
            break
    assert saw_crash, "expected harness_exit{reason:'crash', code:1} in agent0 events.jsonl"


@pytest.mark.e2e
def test_s17_max_duration_timeout(tmp_path: Path) -> None:
    """S17 + §14.9: max_duration_s elapses → result=='timeout', alive_at_timeout==[0,1],
    total_duration_s ∈ [10, 12]. Deterministic via fake-wait agents.
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
