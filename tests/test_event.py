"""Tests for the core event model and its dual-clock invariant."""

from __future__ import annotations

from datetime import timezone

from robotframework_superset.event import Event, EventLevel, monotonic_ns, utc_now


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
