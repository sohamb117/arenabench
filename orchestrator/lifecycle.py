import time
from datetime import UTC, datetime
from typing import Literal, cast

from common.protocol import (
    BootAck,
    Envelope,
    Frame,
    HarnessDead,
    HeartbeatTick,
    Kill0,
    Kill0Response,
    MatchStateChange,
    MatchTerminated,
    PidAnnounce,
)
from orchestrator import heartbeat_scheduler, liveness, winner
from orchestrator._lifecycle_state import AgentState, MatchContext, MatchOutcome

__all__ = ["MatchContext", "MatchOutcome", "run_match"]


def run_match(ctx: MatchContext) -> MatchOutcome:  # noqa: PLR0912, PLR0915
    state_name: str = "IDLE"
    seq = 0

    def mk_env(kind: str, data: Frame, dst: str = "guest_probe") -> Envelope:
        nonlocal seq
        seq += 1
        return Envelope(
            ts=datetime.now(UTC),
            seq=seq,
            src="orchestrator",
            dst=dst,
            kind=kind,
            data=data,
        )

    def transition(new_state: str, reason: str) -> None:
        nonlocal state_name
        env = mk_env(
            "match_state_change",
            MatchStateChange(from_state=state_name, to_state=new_state, reason=reason),
            dst="broadcast",
        )
        ctx.logger.write_envelope(env)
        state_name = new_state

    def finish(result: str, winner_slot: int | None, cause: str) -> MatchOutcome:
        res_lit = cast(Literal["victory", "draw", "timeout", "error"], result)
        out = MatchOutcome(result=res_lit, winner=winner_slot, cause=cause, final_state="DONE")
        term = mk_env(
            "match_terminated",
            MatchTerminated(result=res_lit, winner=winner_slot, cause=cause),
            dst="broadcast",
        )
        ctx.logger.write_envelope(term)
        ctx.logger.write_summary(out.model_dump())
        transition("DONE", cause)
        ctx.logger.close()
        return out

    transition("VM_BOOTING", "run_match_called")

    boot_timeout_s = 60.0
    start_ts = ctx.clock()
    booted = False
    while ctx.clock() - start_ts < boot_timeout_s:
        ctx.vsock_server.send_frame(
            ctx.guest_probe_port, mk_env("boot_ack", BootAck(request_id="b1"))
        )
        env = ctx.vsock_server.recv_frame(ctx.guest_probe_port, ctx.poll_interval_s)
        if env and env.kind == "boot_ack_response":
            booted = True
            ctx.logger.write_envelope(env)
            break

    if not booted:
        transition("VM_BOOT_FAILED", "boot_timeout")
        return finish("error", None, "vm_boot_timeout")

    transition("PROVISIONING", "boot_ack_received")

    prov_timeout_s = 60.0
    start_ts = ctx.clock()
    now = ctx.clock()

    agents = {
        slot: AgentState(slot=slot, last_frame_ts=now, last_heartbeat_ts=now, kill0_ts=now)
        for slot in range(ctx.match_config.n_agents)
    }

    announced: set[int] = set()
    while ctx.clock() - start_ts < prov_timeout_s and len(announced) < ctx.match_config.n_agents:
        for slot, port in ctx.agent_ports.items():
            if slot in announced:
                continue
            env = ctx.vsock_server.recv_frame(port, 0)
            if env:
                ctx.logger.write_envelope(env)
                agents[slot].last_frame_ts = ctx.clock()
                if env.kind == "pid_announce" and isinstance(env.data, PidAnnounce):
                    announced.add(slot)
                    agents[slot].pid = env.data.pid
        if len(announced) == ctx.match_config.n_agents:
            break
        time.sleep(ctx.poll_interval_s)

    if len(announced) < ctx.match_config.n_agents:
        transition("PROVISIONING_FAILED", "provisioning_timeout")
        return finish("error", None, "provisioning_timeout")

    transition("HARNESSES_UP", "all_pids_announced")

    match_start_ts = ctx.clock()
    grace_started_s: float | None = None
    last_alive_count = ctx.match_config.n_agents

    while True:
        now = ctx.clock()
        elapsed_s = now - match_start_ts

        for slot, port in ctx.agent_ports.items():
            st = agents[slot]
            while True:
                env = ctx.vsock_server.recv_frame(port, 0)
                if not env:
                    break
                st.last_frame_ts = now
                ctx.logger.write_envelope(env)
                if env.kind == "llm_request":
                    st.llm_call_start_ts = now
                elif env.kind == "llm_response":
                    st.llm_call_start_ts = None
                    if cast(str, state_name) == "HARNESSES_UP":
                        transition("IN_MATCH", "first_llm_response")
                elif env.kind == "harness_exit":
                    st.vsock_connected = False

        has_pids = any(st.pid is not None for st in agents.values())
        if has_pids:
            for st in agents.values():
                if st.pid is not None:
                    ctx.vsock_server.send_frame(
                        ctx.guest_probe_port,
                        mk_env("kill0", Kill0(request_id=f"k_{st.slot}", pid=st.pid)),
                    )
            while True:
                resp = ctx.vsock_server.recv_frame(ctx.guest_probe_port, 0)
                if not resp:
                    break
                if resp.kind == "kill0_response" and isinstance(resp.data, Kill0Response):
                    ctx.logger.write_envelope(resp)
                    for st in agents.values():
                        if st.pid == resp.data.pid:
                            st.kill0_alive = resp.data.alive
                            st.kill0_ts = now

        for slot, port in ctx.agent_ports.items():
            st = agents[slot]
            if heartbeat_scheduler.should_fire(
                st.to_scheduler(), now, ctx.match_config.heartbeat_interval_s
            ):
                hb = mk_env(
                    "heartbeat_tick",
                    HeartbeatTick(elapsed_s=elapsed_s, turn_hint=0),
                    dst=f"agent{slot}",
                )
                ctx.vsock_server.send_frame(port, hb)
                ctx.logger.write_envelope(hb)
                st.last_heartbeat_ts = now

        alive_set: set[int] = set()
        for slot, st in agents.items():
            if st.dead_emitted:
                continue
            lstate = st.to_liveness()
            if liveness.is_alive(lstate, now, ctx.liveness):
                alive_set.add(slot)
            else:
                st.dead_emitted = True
                cause = liveness.cause_of_death(lstate, now, ctx.liveness)
                dead_env = mk_env(
                    "harness_dead",
                    HarnessDead(slot=slot, cause=cause),
                    dst="broadcast",
                )
                ctx.logger.write_envelope(dead_env)

        if cast(str, state_name) == "HARNESSES_UP" and len(alive_set) < ctx.match_config.n_agents:
            transition("IN_MATCH", "agent_died_pre_llm")

        if cast(str, state_name) not in {"IN_MATCH", "WINNER_GRACE"}:
            if (
                cast(str, state_name) == "HARNESSES_UP"
                and ctx.clock() - match_start_ts > ctx.match_config.max_duration_s
            ):
                transition("ARCHIVING", "max_duration_exceeded")
                time.sleep(ctx.match_config.archive_grace_s)
                return finish("timeout", None, "max_duration_exceeded")
            time.sleep(ctx.poll_interval_s)
            continue

        out = winner.resolve(
            alive=cast("set[winner.AgentSlot]", alive_set),
            elapsed_s=elapsed_s,
            grace_started_s=grace_started_s,
            grace_period_s=ctx.match_config.grace_period_s,
            max_duration_s=ctx.match_config.max_duration_s,
            last_alive_count=last_alive_count,
        )

        if out.result == "in_progress":
            if out.cause == "grace_started" and cast(str, state_name) != "WINNER_GRACE":
                grace_started_s = elapsed_s
                transition("WINNER_GRACE", "grace_started")
        else:
            transition("ARCHIVING", out.cause)
            time.sleep(ctx.match_config.archive_grace_s)
            return finish(out.result, out.winner, out.cause)

        if cast(str, state_name) != "WINNER_GRACE":
            last_alive_count = len(alive_set)
        time.sleep(ctx.poll_interval_s)
