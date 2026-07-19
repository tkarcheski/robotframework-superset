"""Tests for the Superset-backed DatabaseSink (SQLite path).

PostgreSQL behavior shares the same SQLAlchemy code path; these tests pin the
contract on SQLite, which CI always has. The core invariant under test:
BOTH timestamps survive a round-trip — ``wall_clock`` tz-aware to the
microsecond, ``monotonic_ns`` exact.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from robotframework_superset.event import Event, EventLevel
from robotframework_superset.sinks.db import DatabaseSink


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path}/events.db"


def _rows(tmp_path: Path) -> List[Tuple[Any, ...]]:
    con = sqlite3.connect(f"{tmp_path}/events.db")
    try:
        return list(
            con.execute(
                "SELECT event_type, source, wall_clock, monotonic_ns,"
                " level, message, duration_ns, payload FROM events ORDER BY id"
            )
        )
    except sqlite3.OperationalError:
        return []  # schema is created lazily on first flush; no table = no rows
    finally:
        con.close()


def test_round_trip_preserves_both_clocks(tmp_path: Path) -> None:
    sink = DatabaseSink(database_url=_url(tmp_path), batch_size=10)
    event = Event(
        event_type="robot.test.end",
        source="robot",
        level=EventLevel.WARN,
        message="slow test",
        duration_ns=123456789,
        payload={"status": "PASS", "tags": ["smoke"]},
    )
    sink.emit(event)
    sink.close()

    rows = _rows(tmp_path)
    assert len(rows) == 1
    event_type, source, wall_clock, mono, level, message, duration_ns, payload = rows[0]
    assert (event_type, source, level, message) == ("robot.test.end", "robot", "WARN", "slow test")
    assert mono == event.monotonic_ns
    assert duration_ns == 123456789
    # wall_clock persisted as ISO-8601 with offset; parses back tz-aware and
    # equal to the original, microseconds included.
    parsed = datetime.fromisoformat(wall_clock)
    assert parsed.tzinfo is not None
    assert parsed == event.wall_clock
    assert json.loads(payload) == {"status": "PASS", "tags": ["smoke"]}


def test_batching_flushes_at_batch_size_and_on_flush(tmp_path: Path) -> None:
    sink = DatabaseSink(database_url=_url(tmp_path), batch_size=2)
    sink.emit(Event(event_type="e", source="s", message="1"))
    assert _rows(tmp_path) == []  # buffered, not yet written
    sink.emit(Event(event_type="e", source="s", message="2"))
    assert [r[5] for r in _rows(tmp_path)] == ["1", "2"]  # auto-flush at batch_size
    sink.emit(Event(event_type="e", source="s", message="3"))
    sink.flush()
    assert [r[5] for r in _rows(tmp_path)] == ["1", "2", "3"]
    sink.close()


def test_close_flushes_remainder(tmp_path: Path) -> None:
    sink = DatabaseSink(database_url=_url(tmp_path), batch_size=100)
    sink.emit_many(Event(event_type="e", source="s", message=str(i)) for i in range(5))
    sink.close()
    assert len(_rows(tmp_path)) == 5


def test_emit_never_raises_on_backend_failure(capsys: pytest.CaptureFixture[str]) -> None:
    # Unreachable database file — flush fails, but emit must not raise.
    sink = DatabaseSink(database_url="sqlite:////nonexistent-dir/x/y/events.db", batch_size=1)
    sink.emit(Event(event_type="e", source="s"))
    sink.close()
    assert "WARNING" in capsys.readouterr().out


def test_invalid_event_skipped_and_logged(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    sink = DatabaseSink(database_url=_url(tmp_path), batch_size=1)
    sink.emit(Event(event_type="e", source="s", payload={"bad": object()}))
    sink.close()
    assert _rows(tmp_path) == []
    assert "WARNING" in capsys.readouterr().out


def test_missing_database_url_hard_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Per policy: hard-fail only when the work cannot proceed at all.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        DatabaseSink()


def test_database_url_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", _url(tmp_path))
    sink = DatabaseSink(batch_size=1)
    sink.emit(Event(event_type="e", source="s"))
    sink.close()
    assert len(_rows(tmp_path)) == 1
