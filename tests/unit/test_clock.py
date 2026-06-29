import re
import time
from datetime import UTC, datetime, timedelta

import pytest

from common import clock

MIN_MONOTONIC_DELTA_S = 0.005


def test_now_utc_returns_utc_timezone() -> None:
    # Given / When
    dt = clock.now_utc()

    # Then
    assert dt.tzinfo is UTC


def test_now_iso_matches_z_suffix_microseconds() -> None:
    # Given / When
    value = clock.now_iso()

    # Then
    assert re.fullmatch(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$", value)


def test_parse_iso_round_trips_now_iso_within_1ms(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    fixed = datetime(2026, 6, 28, 19, 0, 0, 123456, tzinfo=UTC)

    def fake_now_utc() -> datetime:
        return fixed

    monkeypatch.setattr(clock, "now_utc", fake_now_utc)

    # When
    parsed = clock.parse_iso(clock.now_iso())

    # Then
    assert abs(parsed - fixed) <= timedelta(milliseconds=1)


def test_parse_iso_rejects_non_utc_offset() -> None:
    # When / Then
    with pytest.raises(ValueError):
        clock.parse_iso("2026-06-28T19:00:00.123456+05:00")


def test_parse_iso_rejects_naive_timestamp() -> None:
    # When / Then
    with pytest.raises(ValueError):
        clock.parse_iso("2026-06-28T19:00:00")


def test_now_monotonic_s_increases_over_time() -> None:
    # Given
    start = clock.now_monotonic_s()

    # When
    time.sleep(0.01)
    end = clock.now_monotonic_s()

    # Then
    assert end - start >= MIN_MONOTONIC_DELTA_S


def test_elapsed_s_is_non_negative_after_sleep() -> None:
    # Given
    start = clock.now_monotonic_s()

    # When
    time.sleep(0.01)
    elapsed = clock.elapsed_s(start)

    # Then
    assert elapsed >= 0
