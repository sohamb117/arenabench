import time
from typing import cast

import pytest

from common.protocol import (
    BootAckResponse,
    Envelope,
    HarnessExit,
    LlmResponse,
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
    state_transitions,
)

GRACE_PERIOD_S = 30
SHELL_PORT_0 = 10000
SHELL_PORT_1 = 10001
SHELL_PORT_2 = 10002
SHELL_PORT_3 = 10003
LLM_SCHEDULE_TIME_S = 1.0
DEATH_TIME_S = 30.0
SURVIVOR_DEATH_TIME_S = 45.0
N_AGENTS_4 = 4


def _llm_response_env(turn: int = 1) -> "Envelope":
    return make_env(
        "llm_response",
        LlmResponse(
            turn=turn,
            request_id="r",
            content="hi",
            parser="json",
            parse_ok=True,
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_s=1.0,
            error=None,
        ),
    )


def _crash_env() -> "Envelope":
    return make_env("harness_exit", HarnessExit(reason="crash", code=1, last_turn=1))


def _clean_env() -> "Envelope":
    return make_env("harness_exit", HarnessExit(reason="clean", code=0, last_turn=1))


@pytest.fixture
def base_ctx(monkeypatch: pytest.MonkeyPatch) -> MatchContext:
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


def _vsock(ctx: MatchContext) -> FakeVsockServer:
    assert isinstance(ctx.vsock_server, FakeVsockServer)
    return ctx.vsock_server


def _logger(ctx: MatchContext) -> FakeLogger:
    return cast(FakeLogger, ctx.logger)


def test_s1_happy_victory(base_ctx: MatchContext) -> None:
    vsock = _vsock(base_ctx)
    schedule_boot_and_provision(vsock)
    vsock.schedule(LLM_SCHEDULE_TIME_S, SHELL_PORT_0, _llm_response_env())
    vsock.schedule(DEATH_TIME_S, SHELL_PORT_1, _clean_env())

    outcome = run_match(base_ctx)

    assert outcome.result == "victory"
    assert outcome.winner == 0
    assert outcome.cause == "opponent_crashed"
    assert state_transitions(_logger(base_ctx).envs) == [
        "VM_BOOTING",
        "PROVISIONING",
        "HARNESSES_UP",
        "IN_MATCH",
        "WINNER_GRACE",
        "ARCHIVING",
        "DONE",
    ]


def test_s3_mutual_destruction(base_ctx: MatchContext) -> None:
    vsock = _vsock(base_ctx)
    schedule_boot_and_provision(vsock)
    vsock.schedule(LLM_SCHEDULE_TIME_S, SHELL_PORT_0, _llm_response_env())
    vsock.schedule(DEATH_TIME_S, SHELL_PORT_0, _crash_env())
    vsock.schedule(DEATH_TIME_S, SHELL_PORT_1, _crash_env())

    outcome = run_match(base_ctx)

    assert outcome.result == "draw"
    assert outcome.cause == "mutual_destruction"


def test_s3_survivor_dies_in_grace(base_ctx: MatchContext) -> None:
    vsock = _vsock(base_ctx)
    schedule_boot_and_provision(vsock)
    vsock.schedule(LLM_SCHEDULE_TIME_S, SHELL_PORT_0, _llm_response_env())
    vsock.schedule(DEATH_TIME_S, SHELL_PORT_1, _crash_env())
    vsock.schedule(SURVIVOR_DEATH_TIME_S, SHELL_PORT_0, _crash_env())

    outcome = run_match(base_ctx)

    assert outcome.result == "draw"
    assert outcome.cause == "survivor_died_in_grace"


def test_s15_walkover(base_ctx: MatchContext) -> None:
    vsock = _vsock(base_ctx)
    schedule_boot_and_provision(vsock)
    vsock.schedule(LLM_SCHEDULE_TIME_S, SHELL_PORT_0, _crash_env())

    outcome = run_match(base_ctx)

    assert outcome.result == "victory"
    assert outcome.winner == 1
    assert outcome.cause == "opponent_crashed"


def test_s17_timeout(base_ctx: MatchContext) -> None:
    schedule_boot_and_provision(_vsock(base_ctx))
    outcome = run_match(base_ctx)
    assert outcome.result == "timeout"


def test_vm_boot_failed(base_ctx: MatchContext) -> None:
    outcome = run_match(base_ctx)
    assert outcome.result == "error"
    assert outcome.cause == "vm_boot_timeout"
    assert "VM_BOOT_FAILED" in state_transitions(_logger(base_ctx).envs)


def test_provisioning_failed(base_ctx: MatchContext) -> None:
    vsock = _vsock(base_ctx)
    vsock.schedule(
        0.1,
        PROBE_PORT,
        make_env(
            "boot_ack_response",
            BootAckResponse(request_id="b1", kernel="k", uptime_s=1.0, cid=3),
        ),
    )
    vsock.schedule(
        0.2,
        SHELL_PORT_0,
        make_env(
            "pid_announce",
            PidAnnounce(pid=100, user="u", uid=100, hostname="h", parser="json", model="m"),
        ),
    )

    outcome = run_match(base_ctx)
    assert outcome.result == "error"
    assert outcome.cause == "provisioning_timeout"
    assert "PROVISIONING_FAILED" in state_transitions(_logger(base_ctx).envs)
