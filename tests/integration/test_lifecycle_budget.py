from __future__ import annotations

import time
from typing import cast

import pytest

from common.errors import TransportError
from common.protocol import (
    BootAckResponse,
    BudgetCapability,
    Envelope,
    LlmRequest,
    LlmReservationDecision,
    PidAnnounce,
    Shutdown,
)
from orchestrator.budget_runtime import BudgetRuntime, SlotBudgetPolicy
from orchestrator.lifecycle import MatchContext, run_match
from orchestrator.liveness import LivenessThresholds
from orchestrator.logger import MatchLogger
from orchestrator.match_config import AgentEntry, MatchConfig
from orchestrator.pricing import PricingProfile
from orchestrator.spend_ledger import SpendLimits
from tests.integration._state_machine_fakes import (
    PROBE_PORT,
    FakeLogger,
    FakeVsockServer,
    SimClock,
    make_env,
    make_sleep_patch,
)

PORT_0 = 10000
PORT_1 = 10001
AGENT_COUNT = 2


def _context(
    monkeypatch: pytest.MonkeyPatch, *, cap: float
) -> tuple[MatchContext, FakeVsockServer]:
    clock = SimClock()
    monkeypatch.setattr(time, "sleep", make_sleep_patch(clock))
    server = FakeVsockServer(clock)
    config = MatchConfig(
        match_id="budget-match",
        n_agents=2,
        heartbeat_interval_s=100,
        grace_period_s=1,
        max_duration_s=100,
        archive_grace_s=0,
        budget_usd=cap,
        agents=[
            AgentEntry(slot=0, user="agent0", config="c0"),
            AgentEntry(slot=1, user="agent1", config="c1"),
        ],
    )
    runtime = BudgetRuntime(
        limits=SpendLimits(budget_usd=cap, per_agent_budget_usd=None),
        profiles={
            "provider/model": PricingProfile(
                model="provider/model",
                input_usd_per_token=0.1,
                output_usd_per_token=0.1,
            )
        },
        slots=(0, 1),
        policies={
            slot: SlotBudgetPolicy(model="provider/model", fallback_models=(), max_output_tokens=10)
            for slot in (0, 1)
        },
    )
    return (
        MatchContext(
            match_config=config,
            vsock_server=server,
            guest_probe_port=PROBE_PORT,
            agent_ports={0: PORT_0, 1: PORT_1},
            logger=cast(MatchLogger, FakeLogger()),
            clock=clock,
            liveness=LivenessThresholds(),
            poll_interval_s=1.0,
            budget_runtime=runtime,
        ),
        server,
    )


def _schedule_boot(server: FakeVsockServer) -> None:
    server.schedule(
        0.1,
        PROBE_PORT,
        make_env(
            "boot_ack_response",
            BootAckResponse(request_id="b1", kernel="k", uptime_s=1.0, cid=3),
        ),
    )


def _announce(slot: int, *, capable: bool) -> PidAnnounce:
    return PidAnnounce(
        pid=100 + slot,
        user=f"agent{slot}",
        uid=100 + slot,
        hostname="guest",
        parser="json",
        model="provider/model",
        budget_capability_version=1 if capable else None,
    )


def test_capped_match_rejects_stale_guest_during_provisioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, server = _context(monkeypatch, cap=1.0)
    _schedule_boot(server)
    server.schedule(0.2, PORT_0, make_env("pid_announce", _announce(0, capable=False)))
    server.schedule(0.3, PORT_1, make_env("pid_announce", _announce(1, capable=True)))

    outcome = run_match(ctx)

    assert outcome.result == "error"
    assert outcome.cause == "budget_capability_required"
    assert not any(
        env.kind == "llm_reservation_decision" for env in server.outbound.get(PORT_0, [])
    )


def test_budget_denial_broadcasts_shutdown_and_returns_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, server = _context(monkeypatch, cap=0.5)
    _schedule_boot(server)
    server.schedule(0.2, PORT_0, make_env("pid_announce", _announce(0, capable=True)))
    server.schedule(0.3, PORT_1, make_env("pid_announce", _announce(1, capable=True)))
    server.schedule(
        1.0,
        PORT_0,
        make_env(
            "llm_request",
            LlmRequest(
                turn=0,
                request_id="request",
                model="provider/model",
                messages_count=1,
                prompt_chars=10,
                prompt_tokens=10,
                max_output_tokens=10,
                temperature=0.0,
            ),
        ),
    )

    outcome = run_match(ctx)

    capabilities = [
        env.data
        for port in (PORT_0, PORT_1)
        for env in server.outbound[port]
        if env.kind == "budget_capability"
    ]
    decisions = [
        env.data for env in server.outbound[PORT_0] if env.kind == "llm_reservation_decision"
    ]
    shutdowns = [
        env.data
        for port in (PORT_0, PORT_1)
        for env in server.outbound[port]
        if env.kind == "shutdown"
    ]
    assert all(isinstance(frame, BudgetCapability) and frame.enabled for frame in capabilities)
    assert len(capabilities) == AGENT_COUNT
    assert len(decisions) == 1
    assert isinstance(decisions[0], LlmReservationDecision)
    assert decisions[0].granted is False
    assert all(isinstance(frame, Shutdown) for frame in shutdowns)
    assert len(shutdowns) == AGENT_COUNT
    assert outcome.result == "timeout"
    assert outcome.winner is None
    assert outcome.alive_at_timeout == [0, 1]
    assert outcome.total_duration_s is not None
    assert outcome.estimated_spend_by_agent_usd == {"0": 0.0, "1": 0.0}


def test_budget_denial_ignores_closed_channel_during_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx, server = _context(monkeypatch, cap=0.5)
    _schedule_boot(server)
    server.schedule(0.2, PORT_0, make_env("pid_announce", _announce(0, capable=True)))
    server.schedule(0.3, PORT_1, make_env("pid_announce", _announce(1, capable=True)))
    server.schedule(
        1.0,
        PORT_0,
        make_env(
            "llm_request",
            LlmRequest(
                turn=0,
                request_id="request",
                model="provider/model",
                messages_count=1,
                prompt_chars=10,
                prompt_tokens=10,
                max_output_tokens=10,
                temperature=0.0,
            ),
        ),
    )
    send_frame = server.send_frame

    def fail_closed_shutdown(port: int, env: Envelope) -> None:
        if port == PORT_1 and getattr(env, "kind", None) == "shutdown":
            raise TransportError("ssh transport is closed", port=port)
        send_frame(port, env)

    monkeypatch.setattr(server, "send_frame", fail_closed_shutdown)

    outcome = run_match(ctx)

    assert outcome.result == "timeout"
    assert outcome.cause == "global_budget_exhausted"
    assert outcome.estimated_spend_by_agent_usd == {"0": 0.0, "1": 0.0}
