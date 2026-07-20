"""Tests for the built-in sinks and the BaseSink batching helper."""

from __future__ import annotations

from robotframework_superset.event import Event
from robotframework_superset.sink import Sink
from robotframework_superset.sinks.null import MemorySink, NullSink


def _event(i: int) -> Event:
    return Event(event_type="test.event", source="unit", message=f"e{i}")


def test_null_sink_discards() -> None:
    sink = NullSink()
    sink.emit(_event(0))
    sink.flush()
    sink.close()  # no error, nothing retained


def test_memory_sink_records_in_order() -> None:
    sink = MemorySink()
    sink.emit_many(_event(i) for i in range(3))
    assert [e.message for e in sink.events] == ["e0", "e1", "e2"]


def test_sinks_satisfy_protocol() -> None:
    assert isinstance(NullSink(), Sink)
    assert isinstance(MemorySink(), Sink)
