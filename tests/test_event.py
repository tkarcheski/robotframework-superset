"""Tests for the core event model and its dual-clock invariant."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from robotframework_superset.event import (
    Event,
    EventLevel,
    elapsed_ns,
    monotonic_ns,
    utc_now,
)


def test_utc_now_is_tz_aware_utc() -> None:
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timezone.utc.utcoffset(now)


def test_monotonic_ns_is_monotonic() -> None:
    a = monotonic_ns()
    b = monotonic_ns()
    assert b >= a
    assert isinstance(a, int)


def test_event_defaults_capture_both_clocks() -> None:
    event = Event(event_type="robot.test.end", source="robot")
    assert event.wall_clock.tzinfo is not None
    assert isinstance(event.monotonic_ns, int)
    assert event.level is EventLevel.INFO
    assert event.duration_ns == -1


def test_to_dict_is_json_shaped() -> None:
    event = Event(
        event_type="openai.response",
        source="openai",
        level=EventLevel.WARN,
        message="slow",
        duration_ns=1234,
        payload={"tokens": 42},
    )
    d = event.to_dict()
    assert d["event_type"] == "openai.response"
    assert d["level"] == "WARN"
    assert d["duration_ns"] == 1234
    assert d["payload"] == {"tokens": 42}
    # wall_clock serializes to an ISO-8601 string with an offset.
    assert "T" in d["wall_clock"]
    assert d["wall_clock"].endswith("+00:00")
    # The whole dict round-trips through json.dumps.
    json.dumps(d)


def test_validate_accepts_well_formed_event() -> None:
    event = Event(event_type="robot.test.end", source="robot", payload={"ok": [1, 2.5, None]})
    event.validate()  # no exception


def test_validate_rejects_non_json_payload() -> None:
    event = Event(event_type="robot.log", source="robot", payload={"bad": object()})
    with pytest.raises(ValueError, match="JSON-serializable"):
        event.validate()


def test_to_dict_rejects_non_json_payload() -> None:
    event = Event(event_type="robot.log", source="robot", payload={"bad": {1, 2}})
    with pytest.raises(ValueError, match="JSON-serializable"):
        event.to_dict()


def test_validate_rejects_naive_wall_clock() -> None:
    event = Event(event_type="robot.log", source="robot", wall_clock=datetime(2026, 7, 17))
    with pytest.raises(ValueError, match="tz-aware"):
        event.validate()


def test_validate_rejects_empty_event_type_or_source() -> None:
    with pytest.raises(ValueError, match="event_type"):
        Event(event_type="", source="robot").validate()
    with pytest.raises(ValueError, match="source"):
        Event(event_type="robot.log", source="").validate()


def test_validate_normalizes_non_utc_offsets() -> None:
    # A tz-aware but non-UTC wall_clock is fine; serialization stays ISO-8601.
    from datetime import timedelta

    tz = timezone(timedelta(hours=-7))
    event = Event(event_type="robot.log", source="robot", wall_clock=datetime(2026, 7, 17, tzinfo=tz))
    event.validate()
    assert "-07:00" in event.to_dict()["wall_clock"]


def test_elapsed_ns_from_start_marker() -> None:
    start = monotonic_ns()
    d = elapsed_ns(start)
    assert isinstance(d, int)
    assert d >= 0
    assert elapsed_ns(start) >= d  # time only moves forward
