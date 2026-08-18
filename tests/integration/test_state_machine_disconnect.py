"""Integration tests split out of test_orchestrator_state_machine.py for LOC cap.

Covers:
- S6 N=4 free-for-all (plan §9): observes 4 pid_announce frames + ports 10000..10003
- S11 harness_dead + match_terminated frames with `vsock_disconnect+kill0_dead`
- Round-6 regression: SSH transport drops WITHOUT a harness_exit envelope →
  lifecycle.is_open() check still marks AgentState.vsock_connected=False so
  liveness produces the §9 S11 combined cause.
"""

from __future__ import annotations

import time
from typing import cast

import pytest

from common.protocol import (
    BootAckResponse,
    Envelope,
    HarnessDead,
    HarnessExit,
    LlmResponse,
    MatchTerminated,
    PidAnnounce,
)
from orchestrator.lifecycle import MatchContext, run_match
from orchestrator.liveness import LivenessThresholds
from orchestrator.logger import MatchLogger
from orchestrator.match_config import AgentEntry, MatchConfig
from tests.integration._state_machine_fakes import (
    PROBE_PORT,
    FakeLogger,
    FakeVsockServer,
    SimClock,
    make_env,
    make_sleep_patch,
    schedule_boot_and_provision,
)

GRACE_PERIOD_S = 30
SHELL_PORT_0 = 10000
SHELL_PORT_1 = 10001
SHELL_PORT_2 = 10002
SHELL_PORT_3 = 10003
LLM_SCHEDULE_TIME_S = 1.0
DEATH_TIME_S = 30.0
N_AGENTS_4 = 4


def _llm_response_env() -> Envelope:
    return make_env(
        "llm_response",
        LlmResponse(
            turn=1,
            request_id="r",
            content="hi",
            parser="json",
            parse_ok=True,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_s=1.0,
            parse_error=None,
        ),
    )


def _crash_env() -> Envelope:
    return make_env("harness_exit", HarnessExit(reason="crash", code=1, last_turn=1))


@pytest.fixture
def two_agent_ctx(monkeypatch: pytest.MonkeyPatch) -> MatchContext:
    clock = SimClock()
    monkeypatch.setattr(time, "sleep", make_sleep_patch(clock))
    vsock = FakeVsockServer(clock)
    logger = FakeLogger()
    config = MatchConfig(
        match_id="match-123",
        n_agents=2,
        heartbeat_interval_s=10,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=100,
        archive_grace_s=0,
        network_policy="allowlist",
        agents=[
            AgentEntry(slot=0, user="agent0", config="c0"),
            AgentEntry(slot=1, user="agent1", config="c1"),
        ],
    )
    return MatchContext(
        match_config=config,
        vsock_server=vsock,
        guest_probe_port=PROBE_PORT,
        agent_ports={0: SHELL_PORT_0, 1: SHELL_PORT_1},
        logger=cast(MatchLogger, logger),
        clock=clock,
        liveness=LivenessThresholds(
            silence_threshold_s=1000.0, llm_max_s=300.0, kill0_max_age_s=1000.0
        ),
        poll_interval_s=1.0,
    )


def test_n4_free_for_all_observes_four_pid_announce_and_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S6 plan §9: N=4 free-for-all observes 4 pid_announce frames + ports 10000..10003."""
    clock = SimClock()
    monkeypatch.setattr(time, "sleep", make_sleep_patch(clock))
    vsock = FakeVsockServer(clock)
    logger = FakeLogger()
    agents = [AgentEntry(slot=i, user=f"agent{i}", config=f"c{i}.json") for i in range(N_AGENTS_4)]
    config = MatchConfig(
        match_id="match-n4",
        n_agents=N_AGENTS_4,
        heartbeat_interval_s=10,
        grace_period_s=GRACE_PERIOD_S,
        max_duration_s=100,
        archive_grace_s=0,
        network_policy="allowlist",
        agents=agents,
    )
    agent_ports = {0: SHELL_PORT_0, 1: SHELL_PORT_1, 2: SHELL_PORT_2, 3: SHELL_PORT_3}
    ctx = MatchContext(
        match_config=config,
        vsock_server=vsock,
        guest_probe_port=PROBE_PORT,
        agent_ports=agent_ports,
        logger=cast(MatchLogger, logger),
        clock=clock,
        liveness=LivenessThresholds(
            silence_threshold_s=1000.0, llm_max_s=300.0, kill0_max_age_s=1000.0
        ),
        poll_interval_s=1.0,
    )

    vsock.schedule(
        0.1,
        PROBE_PORT,
        make_env(
            "boot_ack_response",
            BootAckResponse(request_id="b1", kernel="k", uptime_s=1.0, cid=3),
        ),
    )
    for slot in range(N_AGENTS_4):
        vsock.schedule(
            0.2 + slot * 0.1,
            agent_ports[slot],
            make_env(
                "pid_announce",
                PidAnnounce(
                    pid=100 + slot,
                    user=f"agent{slot}",
                    uid=100 + slot,
                    hostname="h",
                    parser="json",
                    model="m",
                ),
            ),
        )
    vsock.schedule(LLM_SCHEDULE_TIME_S, SHELL_PORT_0, _llm_response_env())
    for slot in range(N_AGENTS_4 - 1):
        vsock.schedule(DEATH_TIME_S + slot, agent_ports[slot + 1], _crash_env())

    outcome = run_match(ctx)

    assert outcome.result == "victory"
    assert outcome.winner == 0
    pid_announces = [e for e in logger.envs if e.kind == "pid_announce"]
    assert len(pid_announces) == N_AGENTS_4, f"expected {N_AGENTS_4}, got {len(pid_announces)}"
    assert ctx.agent_ports == {0: 10000, 1: 10001, 2: 10002, 3: 10003}


def test_harness_dead_and_match_terminated_frames(two_agent_ctx: MatchContext) -> None:
    """S11 binary observable per plan §9: cause is '+'-joined when both signals fail."""
    vsock = cast(FakeVsockServer, two_agent_ctx.vsock_server)
    schedule_boot_and_provision(vsock)
    vsock.mark_dead(100)
    vsock.schedule(LLM_SCHEDULE_TIME_S, SHELL_PORT_0, _crash_env())
    run_match(two_agent_ctx)

    envs = cast(FakeLogger, two_agent_ctx.logger).envs
    dead = [e for e in envs if e.kind == "harness_dead" and isinstance(e.data, HarnessDead)]
    term = [e for e in envs if e.kind == "match_terminated" and isinstance(e.data, MatchTerminated)]
    assert len(dead) == 1
    dead_data = dead[0].data
    assert isinstance(dead_data, HarnessDead)
    assert dead_data.slot == 0
    assert dead_data.cause == "vsock_disconnect+kill0_dead"
    assert len(term) == 1
    term_data = term[0].data
    assert isinstance(term_data, MatchTerminated)
    assert term_data.result == "victory"
    assert term_data.winner == 1


def test_transport_drop_without_harness_exit_still_produces_s11_cause(
    two_agent_ctx: MatchContext,
) -> None:
    """Round-6 Gap-4 lock: an SSH subprocess dying produces no `harness_exit`
    envelope, but lifecycle's is_open() probe must still flip
    AgentState.vsock_connected=False so liveness emits §9 S11 cause.
    """
    vsock = cast(FakeVsockServer, two_agent_ctx.vsock_server)
    schedule_boot_and_provision(vsock)
    vsock.mark_dead(100)
    vsock.schedule(LLM_SCHEDULE_TIME_S, SHELL_PORT_0, _llm_response_env())
    vsock.close_port(SHELL_PORT_0)
    run_match(two_agent_ctx)

    envs = cast(FakeLogger, two_agent_ctx.logger).envs
    dead = [e for e in envs if e.kind == "harness_dead" and isinstance(e.data, HarnessDead)]
    assert len(dead) == 1
    dead_data = dead[0].data
    assert isinstance(dead_data, HarnessDead)
    assert dead_data.slot == 0
    assert dead_data.cause == "vsock_disconnect+kill0_dead"
