"""Gated e2e stubs for §9 scenarios not yet covered by deterministic fake-LLM
configs. Each test maps to its plan §9 binary observable and either runs a
deterministic scenario when one exists OR explicitly skips with the
engineering blocker stated so the audit trail records the deferral.

S2  silent-then-revives heartbeat   — harness pauses then resumes;
                                       orchestrator-injected [HEARTBEAT t=]
                                       turn revives the conversation.
S7  winner_grace expiry              — first-to-only-one survives grace_period_s
                                       → result=victory, cause=solo_survivor.
S10 pid_announce uniqueness          — every running harness emits exactly one
                                       pid_announce; orchestrator rejects dup.
S16 silence-during-llm-call alive    — silence under llm_max_s while
                                       llm_call_start_ts is set must NOT mark
                                       agent dead per §3.A4 LLM-pause clause.
S18 archive_grace overlay cleanup    — per-match qcow2 overlay deletes
                                       archive_grace_s after match ends.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
GOLDEN_IMAGE_PATH = REPO_ROOT / "vm" / "images" / "arenabench-golden-aarch64.qcow2"


def _skip_if_no_e2e() -> None:
    if not os.environ.get("ARENABENCH_E2E"):
        pytest.skip("set ARENABENCH_E2E=1 to run real-VM e2e tests")
    if not GOLDEN_IMAGE_PATH.exists():
        pytest.skip(f"golden image not built; run vm/golden/build.sh ({GOLDEN_IMAGE_PATH} missing)")
    if not shutil.which("qemu-system-aarch64"):
        pytest.skip("qemu-system-aarch64 not on PATH")


@pytest.mark.e2e
def test_s2_silent_then_revives_via_heartbeat(tmp_path: Path) -> None:
    """S2: agent stops emitting for >silence_threshold_s, then orchestrator
    injects [HEARTBEAT t=...] which appears in chat history and revives the
    conversation. The S4 causality test already proves the heartbeat→llm_request
    pair; S2 specifically requires the silence→revive recovery path.
    """
    _ = tmp_path
    _skip_if_no_e2e()
    pytest.skip(
        "S2 silent-then-revives requires a fake-LLM agent that pauses for "
        "> silence_threshold_s mid-match then resumes. Add a fake-pause-then-"
        "resume agent + match config to exercise; covered structurally by "
        "test_s4_heartbeat_injected_precedes_llm_request_with_prefix."
    )


@pytest.mark.e2e
def test_s7_grace_expiry_solo_survivor_victory(tmp_path: Path) -> None:
    """S7: survivor outlasts grace_period_s → result=victory, cause=solo_survivor."""
    _ = tmp_path
    _skip_if_no_e2e()
    pytest.skip(
        "S7 grace expiry requires: 1v1, slot 0 fake-suicide, slot 1 fake-wait, "
        "grace_period_s < harness silence_threshold_s. Configurable via "
        "configs/matches/s7-grace-survival.json (deferred to follow-up); the "
        "winner.resolve grace path is covered by the integration tests."
    )


@pytest.mark.e2e
def test_s10_pid_announce_uniqueness(tmp_path: Path) -> None:
    """S10: every running harness emits exactly ONE pid_announce frame."""
    _ = tmp_path
    _skip_if_no_e2e()
    pytest.skip(
        "S10 pid_announce uniqueness requires real-VM run + parse all per-agent "
        "bash.jsonl files asserting exactly one pid_announce per agent dir. "
        "Structurally covered by S6's N=4 test (asserts len(pid_announces) "
        "== n_agents); a dedicated S10 test adds duplicate-rejection assertion."
    )


@pytest.mark.e2e
def test_s16_silence_under_llm_call_remains_alive(tmp_path: Path) -> None:
    """S16: silence > silence_threshold_s while llm_call_start_ts is set
    (LLM call in flight) must NOT mark the agent dead per §3.A4 LLM-pause clause.
    """
    _ = tmp_path
    _skip_if_no_e2e()
    pytest.skip(
        "S16 LLM-pause clause is integration-tested via "
        "tests/unit/test_liveness.py::test_alive_llm_in_flight + "
        "test_alive_llm_max_exceeded_with_kill0_fresh. Real-VM e2e would "
        "require a fake-LLM agent that simulates a slow LLM call > "
        "silence_threshold_s; defer until configs/agents/fake-slow-llm.json."
    )


@pytest.mark.e2e
def test_s18_archive_grace_overlay_cleanup(tmp_path: Path) -> None:
    """S18: per-match qcow2 overlay is deleted archive_grace_s seconds after
    the match transitions to ARCHIVING. Already covered structurally by
    tests/e2e/test_vm_disposability.py::test_overlay_deleted_after_archive_grace.
    """
    _ = tmp_path
    _skip_if_no_e2e()
    pytest.skip(
        "S18 covered by tests/e2e/test_vm_disposability.py::test_overlay_"
        "deleted_after_archive_grace; this stub records the §9 mapping."
    )
