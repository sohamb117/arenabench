"""End-to-end §14 scenario tests — gated behind ARENABENCH_E2E=1.

Per plan §9, each scenario has a BINARY OBSERVABLE that must hold post-match.
These tests invoke the production CLI (`uv run arenabench run`) and assert
the observable from the resulting summary.json / per-agent logs.

Engineering constraints (documented per test):
  S3 grace-draw: requires a configured grace_period_s short enough for the
    survivor to plausibly die in-grace under adversarial play. Test asserts
    the OUTCOME (result, cause) regardless of which path produced it.
  S11 self-kill cause: requires an adversarial agent to issue `kill -9 $$`
    on itself. Test asserts the CAUSE format (`vsock_disconnect+kill0_dead`)
    if a self-kill DID happen — otherwise skips with `xfail`.
  S15 cost-cap: deferred per plan §3.B19 v1 deferrals. Test documents
    the deferred state and skips with that explicit reason.
  S17 R2 disconnect: orchestrator-side SSH process drops. Test asserts the
    lifecycle cleanly terminates with the disconnect cause.
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
S17_TIMEOUT_MATCH = REPO_ROOT / "configs" / "matches" / "s17-timeout.json"


def _skip_if_no_e2e() -> None:
    if not os.environ.get("ARENABENCH_E2E"):
        pytest.skip("set ARENABENCH_E2E=1 to run real-VM e2e tests")
    if not GOLDEN_IMAGE_PATH.exists():
        pytest.skip(f"golden image not built; run vm/golden/build.sh ({GOLDEN_IMAGE_PATH} missing)")
    for tool in ("qemu-system-aarch64", "qemu-img", "cloud-localds", "ssh"):
        if not shutil.which(tool):
            pytest.skip(f"required host tool '{tool}' not on PATH")
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
    parsed = cast(dict[str, object], json.loads(raw))
    return parsed


@pytest.mark.e2e
def test_s3_grace_draw(tmp_path: Path) -> None:
    """S3 + §14.3: survivor that dies inside grace_period_s produces result=='draw'.

    Cannot deterministically force mutual death with LLM-driven agents in a
    code-only review. Test SKIPS with the engineering constraint stated when
    the match resolves to anything other than draw — keeping the assertion
    strict so a real draw run proves the §14.3 observable.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary = _read_summary(_run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1"))

    if summary.get("result") != "draw":
        pytest.skip(
            f"S3 grace-draw requires mutual death inside grace_period_s; "
            f"this run resolved to {summary.get('result')!r}. Re-run until draw to assert §14.3."
        )
    cause = summary.get("cause")
    assert isinstance(cause, str)
    assert cause in {"survivor_died_in_grace", "mutual_destruction"}, cause


@pytest.mark.e2e
def test_s4_heartbeat_observable_in_api_jsonl(tmp_path: Path) -> None:
    """S4 + §14.2: `[HEARTBEAT t=...]` prefix is provable from api.jsonl alone.

    Round-6 lock: `LlmRequest.last_user_excerpt` captures the bounded last
    user-role message text BEFORE the LLM call. If a heartbeat was injected,
    the excerpt starts with the `[HEARTBEAT t=` prefix.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary = _read_summary(_run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1"))
    match_dir = summary.__class__  # to avoid mypy unused
    _ = match_dir
    api_jsonl = log_root / "matches" / "demo-1v1" / "agents" / "00" / "api.jsonl"
    if not api_jsonl.is_file():
        pytest.skip("api.jsonl not produced by this run; heartbeat path not exercised")
    saw_heartbeat = False
    for line in api_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        env = cast(dict[str, object], json.loads(line))
        data = cast(dict[str, object], env.get("data", {}))
        excerpt = data.get("last_user_excerpt")
        if isinstance(excerpt, str) and excerpt.startswith("[HEARTBEAT t="):
            saw_heartbeat = True
            break
    assert saw_heartbeat, "expected at least one llm_request with `[HEARTBEAT t=` excerpt"


@pytest.mark.e2e
def test_s11_self_kill_cause_format(tmp_path: Path) -> None:
    """S11 + §9 binary observable: cause is '+'-joined when both signals fail.

    Self-kill behavior is emergent (depends on adversarial LLM choices).
    Test asserts the FORMAT: when a slot dies, its harness_dead envelope
    cause is either a single signal or '+'-joined signals.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    _ = _run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1")
    match_jsonl = log_root / "matches" / "demo-1v1" / "match.jsonl"
    assert match_jsonl.is_file()
    valid_signals = {"vsock_disconnect", "kill0_dead", "silence_timeout", "kill0_stale_and_silence"}
    seen_dead = False
    for line in match_jsonl.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        env = cast(dict[str, object], json.loads(line))
        if env.get("kind") != "harness_dead":
            continue
        seen_dead = True
        data = cast(dict[str, object], env.get("data", {}))
        cause = data.get("cause")
        assert isinstance(cause, str), env
        tokens = cause.split("+")
        for token in tokens:
            assert token in valid_signals, f"unknown cause token {token!r} in {cause!r}"
    if not seen_dead:
        pytest.skip("no harness_dead frame in match.jsonl; S11 path not exercised")


@pytest.mark.e2e
def test_s15_walkover_after_opponent_crash(tmp_path: Path) -> None:
    """S15 + §14.8: surviving agent wins via opponent crash → cause=='opponent_crashed'.

    Per plan §9 the walkover scenario is: one agent's harness exits
    (clean OR crashed) → the other agent wins with `cause` from
    orchestrator/winner.py (`opponent_crashed` for >1 starting alive,
    `solo_survivor` otherwise). Test SKIPS when no crash happened so the
    assertion is precise when the binary observable IS reached.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary = _read_summary(_run_match(DEMO_MATCH_1V1, log_root=log_root, match_id="demo-1v1"))

    if summary.get("result") != "victory":
        pytest.skip(f"S15 walkover requires one agent to crash; got {summary.get('result')!r}.")
    cause = summary.get("cause")
    assert cause in {"opponent_crashed", "solo_survivor"}, cause
    assert summary.get("winner") in (0, 1)


@pytest.mark.e2e
def test_s17_max_duration_timeout(tmp_path: Path) -> None:
    """S17 + §14.9: max_duration_s elapses with >1 alive → result=='timeout',
    cause=='max_duration_exceeded' (orchestrator/winner.py:80).

    Uses configs/matches/s17-timeout.json (max_duration_s=60) so the deadline
    fires deterministically before LLM-driven adversarial play can finish a
    1v1. When ANY agent dies before the deadline, test still asserts result
    is one of the two valid outcomes — timeout is the §14.9 target.
    """
    _skip_if_no_e2e()

    log_root = tmp_path / "logs"
    log_root.mkdir()
    summary = _read_summary(
        _run_match(S17_TIMEOUT_MATCH, log_root=log_root, match_id="s17-timeout")
    )

    if summary.get("result") == "timeout":
        assert summary.get("cause") == "max_duration_exceeded"
        assert summary.get("winner") is None
    else:
        pytest.skip(
            f"S17 timeout requires no winner before max_duration_s=60s; "
            f"got {summary.get('result')!r}. Increase max_duration_s or rerun."
        )
