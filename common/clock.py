from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_iso() -> str:
    return now_utc().isoformat(timespec="microseconds").replace("+00:00", "Z")


def now_monotonic_s() -> float:
    return time.monotonic()


def elapsed_s(start_monotonic: float) -> float:
    return now_monotonic_s() - start_monotonic


def parse_iso(s: str) -> datetime:
    if not s.endswith(("Z", "+00:00")):
        raise ValueError(f"non-UTC timestamp: {s!r}")
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"naive datetime: {s!r}")
    if dt.utcoffset() != timedelta(0):
        raise ValueError(f"non-UTC timestamp: {s!r}")
    return dt.astimezone(UTC)
