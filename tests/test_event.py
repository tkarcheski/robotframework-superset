"""Tests for the core event model and its dual-clock invariant."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from robotframework_superset.event import (
    Event,
    EventLevel,
    duration_since,
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
    json.dumps(d)
    # wall_clock serializes to an ISO-8601 string with an offset.
    assert "T" in d["wall_clock"]
    assert d["wall_clock"].endswith("+00:00")


def test_wall_clock_is_normalized_to_utc() -> None:
    event = Event(
        event_type="test.clock",
        source="unit",
        wall_clock=datetime.fromisoformat("2026-07-16T12:34:56.123456-05:00"),
    )
    assert event.wall_clock.utcoffset() == timezone.utc.utcoffset(event.wall_clock)
    assert event.to_dict()["wall_clock"] == "2026-07-16T17:34:56.123456+00:00"


def test_naive_wall_clock_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Event(
            event_type="test.clock",
            source="unit",
            wall_clock=datetime(2026, 7, 16, 12, 34, 56),
        )


def test_non_datetime_wall_clock_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="datetime"):
        Event(event_type="test.clock", source="unit", wall_clock="invalid")  # type: ignore[arg-type]


def test_naive_wall_clock_mutation_is_rejected() -> None:
    event = Event(event_type="test.clock", source="unit")
    event.wall_clock = datetime(2026, 7, 16, 12, 34, 56)
    with pytest.raises(ValueError, match="timezone-aware"):
        event.to_dict()


@pytest.mark.parametrize("payload", [{"bad": {1, 2}}, {"bad": float("nan")}])
def test_non_json_payload_is_rejected(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="JSON-serializable"):
        Event(event_type="test.payload", source="unit", payload=payload)


def test_mutated_invalid_payload_is_caught_by_to_dict() -> None:
    event = Event(event_type="test.payload", source="unit")
    event.payload["bad"] = object()
    with pytest.raises(ValueError, match="JSON-serializable"):
        event.to_dict()


def test_event_type_must_be_dotted_lower_case() -> None:
    with pytest.raises(ValueError, match="lower-case dotted"):
        Event(event_type="TEST", source="unit")


def test_duration_since_uses_monotonic_markers() -> None:
    assert duration_since(10, 25) == 15
    start = monotonic_ns()
    assert duration_since(start) >= 0
    with pytest.raises(ValueError, match="non-negative"):
        duration_since(-1)
    with pytest.raises(ValueError, match="greater than or equal"):
        duration_since(25, 10)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("wall_clock", "not-a-datetime", "datetime"),
        ("source", "", "non-empty"),
        ("monotonic_ns", "1", "integer"),
        ("monotonic_ns", -1, "non-negative"),
        ("level", "INFO", "EventLevel"),
        ("message", 3, "string"),
        ("duration_ns", "1", "integer"),
        ("duration_ns", -2, "-1 or"),
        ("payload", [], "dictionary"),
    ],
)
def test_mutated_invalid_fields_are_rejected(field: str, value: object, message: str) -> None:
    event = Event(event_type="test.validation", source="unit")
    setattr(event, field, value)
    with pytest.raises(ValueError, match=message):
        event.validate()
