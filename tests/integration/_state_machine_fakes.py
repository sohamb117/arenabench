from datetime import UTC, datetime
from typing import cast

from common.protocol import (
    BootAckResponse,
    Envelope,
    Frame,
    HarnessExit,
    Kill0,
    Kill0Response,
    LlmResponse,
    MatchStateChange,
    PidAnnounce,
)

PROBE_PORT = 9999


class SimClock:
    def __init__(self) -> None:
        self.now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, s: float) -> None:
        self.now += s


class FakeVsockServer:
    def __init__(self, clock: SimClock) -> None:
        self.clock = clock
        self.inbound: dict[int, list[Envelope]] = {}
        self.scheduled: list[tuple[float, int, Envelope]] = []
        self.outbound: dict[int, list[Envelope]] = {}
        self.auto_kill0: bool = True
        self.dead_pids: set[int] = set()
        self.closed_ports: set[int] = set()

    def mark_dead(self, pid: int) -> None:
        """Cause subsequent auto_kill0 responses for this pid to report alive=False."""
        self.dead_pids.add(pid)

    def close_port(self, port: int) -> None:
        """Simulate the transport for `port` dropping (ssh subprocess death)."""
        self.closed_ports.add(port)

    def is_open(self, port: int) -> bool:
        return port not in self.closed_ports

    def schedule(self, delay_s: float, port: int, env: Envelope) -> None:
        self.scheduled.append((self.clock.now + delay_s, port, env))

    def recv_frame(self, port: int, timeout_s: float | None) -> Envelope | None:
        ready = [x for x in self.scheduled if self.clock.now >= x[0]]
        self.scheduled = [x for x in self.scheduled if self.clock.now < x[0]]
        for _, p, env in ready:
            self.inbound.setdefault(p, []).append(env)
        if self.inbound.get(port):
            return self.inbound[port].pop(0)
        if timeout_s is not None and timeout_s > 0:
            self.clock.advance(timeout_s)
        return None

    def send_frame(self, port: int, env: Envelope) -> None:
        self.outbound.setdefault(port, []).append(env)
        if port == PROBE_PORT and env.kind == "kill0" and self.auto_kill0:
            kill0 = cast(Kill0, env.data)
            alive = kill0.pid not in self.dead_pids
            self.inbound.setdefault(PROBE_PORT, []).append(
                Envelope(
                    ts=datetime.now(UTC),
                    seq=0,
                    src="guest_probe",
                    dst="orchestrator",
                    kind="kill0_response",
                    data=Kill0Response(
                        request_id=kill0.request_id,
                        pid=kill0.pid,
                        alive=alive,
                    ),
                )
            )


class FakeLogger:
    def __init__(self) -> None:
        self.envs: list[Envelope] = []
        self.summary: dict[str, object] | None = None

    def write_envelope(self, env: Envelope) -> None:
        self.envs.append(env)

    def write_summary(self, summary: dict[str, object]) -> None:
        self.summary = summary

    def close(self) -> None:
        return None


def make_sleep_patch(clock: SimClock):
    def patch(s: float) -> None:
        clock.advance(s)

    return patch


def make_env(kind: str, data: Frame) -> Envelope:
    return Envelope(ts=datetime.now(UTC), seq=0, src="x", dst="y", kind=kind, data=data)


def llm_response_env(turn: int = 1) -> Envelope:
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
            parse_error=None,
        ),
    )


def crash_env() -> Envelope:
    return make_env("harness_exit", HarnessExit(reason="crash", code=1, last_turn=1))


def clean_env() -> Envelope:
    return make_env("harness_exit", HarnessExit(reason="clean", code=0, last_turn=1))


def schedule_boot_and_provision(vsock: FakeVsockServer) -> None:
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
        10000,
        make_env(
            "pid_announce",
            PidAnnounce(pid=100, user="u", uid=100, hostname="h", parser="json", model="m"),
        ),
    )
    vsock.schedule(
        0.3,
        10001,
        make_env(
            "pid_announce",
            PidAnnounce(pid=101, user="u", uid=101, hostname="h", parser="json", model="m"),
        ),
    )


def state_transitions(envs: list[Envelope]) -> list[str]:
    return [
        e.data.to_state
        for e in envs
        if e.kind == "match_state_change" and isinstance(e.data, MatchStateChange)
    ]
