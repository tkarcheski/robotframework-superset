"""DatabaseSink round-trip and batching tests using SQLite."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import timezone
from typing import Iterator

import pytest
from sqlalchemy import func, inspect, select

from robotframework_superset.event import Event, EventLevel
from robotframework_superset.sinks.db import DatabaseSink, events_table


def _event(index: int) -> Event:
    return Event(
        event_type="robot.test.end",
        source="robot",
        level=EventLevel.ERROR if index == 2 else EventLevel.INFO,
        message=f"event-{index}",
        duration_ns=index * 100,
        payload={"index": index},
    )


def test_sqlite_round_trip_preserves_both_clocks_and_payload() -> None:
    sink = DatabaseSink("sqlite+pysqlite:///:memory:", batch_size=2)
    events = [_event(index) for index in range(3)]
    sink.emit_many(events)

    with sink.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(events_table)) == 2

    sink.flush()
    with sink.engine.connect() as connection:
        rows = connection.execute(select(events_table).order_by(events_table.c.id)).mappings()
        stored = list(rows)

    assert len(stored) == 3
    assert stored[0]["wall_clock"].tzinfo is not None
    assert stored[0]["wall_clock"].utcoffset() == timezone.utc.utcoffset(stored[0]["wall_clock"])
    assert stored[0]["monotonic_ns"] == events[0].monotonic_ns
    assert stored[2]["payload"] == {"index": 2}
    assert stored[2]["level"] == "ERROR"
    sink.close()


def test_schema_has_required_indexes() -> None:
    sink = DatabaseSink("sqlite+pysqlite:///:memory:")
    names = {item["name"] for item in inspect(sink.engine).get_indexes("events")}
    assert names == {"events_type_source_idx", "events_wall_clock_idx"}
    sink.close()


def test_emit_snapshots_payload_before_buffered_flush() -> None:
    sink = DatabaseSink("sqlite+pysqlite:///:memory:", batch_size=50)
    event = _event(1)
    sink.emit(event)
    event.payload["index"] = 999
    sink.flush()
    with sink.engine.connect() as connection:
        stored = connection.execute(select(events_table)).mappings().one()
    assert stored["payload"] == {"index": 1}
    sink.close()


def test_close_flushes_and_is_idempotent() -> None:
    sink = DatabaseSink("sqlite+pysqlite:///:memory:", batch_size=50)
    sink.emit(_event(1))
    sink.close()
    sink.close()
    with pytest.raises(RuntimeError, match="closed"):
        sink.emit(_event(2))


def test_transient_flush_failure_is_retained_for_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = DatabaseSink("sqlite+pysqlite:///:memory:", batch_size=1)
    original_begin = sink.engine.begin

    @contextmanager
    def failing_begin() -> Iterator[None]:
        raise OSError("temporarily unavailable")
        yield

    monkeypatch.setattr(sink.engine, "begin", failing_begin)
    sink.emit(_event(1))
    assert len(sink._buffer) == 1

    monkeypatch.setattr(sink.engine, "begin", original_begin)
    sink.flush()
    assert sink._buffer == []
    sink.close()


@pytest.mark.parametrize("batch_size", [0, -1])
def test_invalid_batch_size_is_rejected(batch_size: int) -> None:
    with pytest.raises(ValueError, match="batch_size"):
        DatabaseSink("sqlite+pysqlite:///:memory:", batch_size=batch_size)
