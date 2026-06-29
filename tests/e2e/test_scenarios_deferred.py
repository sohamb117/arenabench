"""Gated e2e stubs for §9 scenarios not yet covered by deterministic configs.

Each test maps to the EXACT plan §9 binary observable and either runs a
deterministic scenario when one exists OR skips with the engineering blocker
stated so the audit trail records the deferral. Mappings verified against
`.omo/plans/arenabench-build.md` §9 table.

S2  Explicit kill detection         — winner does `kill -9` against opponent's
                                       pid; agent0/bash.jsonl shows the kill
                                       command with exit_status==0.
S7  Provider swap via config        — two runs with different agents/*.json
                                       produce valid summary.json with
                                       differing api.jsonl.data.model values.
S9  Network allowlist enforced      — agent's `curl https://example.com`
                                       returns nonzero under network_policy=
                                       allowlist; LLM API call still succeeds.
S10 Network full allows egress      — `curl https://example.com` returns
                                       exit 0 under network_policy=full.
S16 LLM 429 retried doesn't count   — fake LLM 429 first, success second;
    as silence                        api.jsonl has 2 llm_request frames for
                                       same turn; orchestrator does NOT
                                       classify agent as dead.
S18 validate rejects bad config     — covered by tests/unit/test_match_config.py
                                       (not real-VM e2e; validate is a pure
                                       CLI parse).
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
def test_s2_explicit_kill_detection(tmp_path: Path) -> None:
    """S2 per §9: agent0/bash.jsonl shows `kill -9 <opponent_pid>` with
    exit_status==0; summary.winner==agent0; time-to-victory < 30s.
    """
    _ = tmp_path
    _skip_if_no_e2e()
    pytest.skip(
        "S2 explicit kill requires a deterministic fake-LLM agent that "
        "issues `kill -9 <opponent_pid>` after reading the opponent's pid "
        "from `ps`. Add configs/agents/fake-explicit-killer.json + "
        "configs/matches/s2-explicit-kill.json."
    )


@pytest.mark.e2e
def test_s7_provider_swap_via_config(tmp_path: Path) -> None:
    """S7 per §9: two runs with different agents/*.json produce valid
    summary.json; api.jsonl[].data.model differs between runs.
    """
    _ = tmp_path
    _skip_if_no_e2e()
    pytest.skip(
        "S7 provider swap requires two consecutive runs with different "
        "configs/agents/*.json variants. Structurally exercised by the "
        "passive-claude.json + passive-gpt.json pair already in the repo."
    )


@pytest.mark.e2e
def test_s9_network_allowlist_enforced(tmp_path: Path) -> None:
    """S9 per §9: under network_policy=allowlist, `curl https://example.com`
    fails; LLM API call still succeeds.
    """
    _ = tmp_path
    _skip_if_no_e2e()
    pytest.skip(
        "S9 allowlist requires the dnsmasq-on-loopback stub (deferred B10 "
        "follow-up). Until dnsmasq lands the allowlist permits DNS to any "
        "upstream so example.com resolves and returns; S9 is blocked on "
        "that follow-up."
    )


@pytest.mark.e2e
def test_s10_network_full_allows_egress(tmp_path: Path) -> None:
    """S10 per §9: under network_policy=full, `curl https://example.com`
    returns exit 0.
    """
    _ = tmp_path
    _skip_if_no_e2e()
    pytest.skip(
        "S10 full-egress requires a deterministic fake-LLM agent that "
        "emits `curl https://example.com` + a match config with "
        "network_policy=full. Add configs/matches/s10-full-egress.json."
    )


@pytest.mark.e2e
def test_s16_llm_429_retry_observable_in_api_jsonl(tmp_path: Path) -> None:
    """S16 per §9: api.jsonl has 2 llm_request frames for the same turn when
    the first attempt 429s and the second succeeds; orchestrator does NOT
    classify the agent as dead.
    """
    _ = tmp_path
    _skip_if_no_e2e()
    pytest.skip(
        "S16 retry observable requires the LLM retry loop to emit one "
        "llm_request envelope per attempt (currently the retry is internal "
        "to harness.llm.call so only the final attempt is logged). Move "
        "the retry loop into harness.loop._llm_turn, or add an on_attempt "
        "callback to llm.call."
    )


@pytest.mark.e2e
def test_s18_validate_rejects_bad_config_is_unit_tested() -> None:
    """S18 per §9: `arenabench validate configs/matches/invalid.json` exits
    nonzero with stderr listing field violations. NOT a real-VM e2e — pure
    CLI parse; covered by tests/unit/test_match_config.py rejection cases.
    """
    pytest.skip(
        "S18 is exercised by tests/unit/test_match_config.py rejection "
        "cases; this stub records the §9 mapping for the audit trail."
    )
