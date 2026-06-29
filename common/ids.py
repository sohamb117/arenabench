from re import compile as re_compile
from typing import NewType
from uuid import uuid4

MatchId = NewType("MatchId", str)
AgentSlot = NewType("AgentSlot", int)
RequestId = NewType("RequestId", str)
Pid = NewType("Pid", int)

_MATCH_ID_RE = re_compile(r"^[A-Za-z0-9_-]+$")
_MAX_MATCH_ID_LEN = 64
_MAX_AGENTS = 16
_PID_MAX = 1 << 22


def make_match_id(s: str) -> MatchId:
    if not s or len(s) > _MAX_MATCH_ID_LEN or not _MATCH_ID_RE.fullmatch(s):
        raise ValueError(f"invalid match_id: {s!r}")
    return MatchId(s)


def make_agent_slot(i: int) -> AgentSlot:
    if i < 0 or i >= _MAX_AGENTS:
        raise ValueError(f"invalid agent_slot: {i}")
    return AgentSlot(i)


def make_request_id() -> RequestId:
    return RequestId(uuid4().hex)


def make_pid(i: int) -> Pid:
    if i <= 0 or i > _PID_MAX:
        raise ValueError(f"invalid pid: {i}")
    return Pid(i)
