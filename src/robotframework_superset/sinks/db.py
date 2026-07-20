"""Superset-backed database sink.

Persists events to a SQL database (PostgreSQL in production, SQLite for local
runs and tests) that Apache Superset reads for dashboards. Both timestamps
are stored on every row — ``wall_clock`` as ``TIMESTAMPTZ`` and
``monotonic_ns`` as ``BIGINT`` — per the core invariant.

Proposed base schema (finalized in the "DB sink + schema" issue)::

    CREATE TABLE events (
        id            BIGSERIAL PRIMARY KEY,
        event_type    TEXT        NOT NULL,
        source        TEXT        NOT NULL,
        wall_clock    TIMESTAMPTZ NOT NULL,   -- ISO-8601 µs + tz
        monotonic_ns  BIGINT      NOT NULL,   -- durations / intra-proc order
        level         TEXT        NOT NULL,
        message       TEXT        NOT NULL DEFAULT '',
        duration_ns   BIGINT      NOT NULL DEFAULT -1,
        payload       JSONB       NOT NULL DEFAULT '{}'
    );
    CREATE INDEX events_wall_clock_idx ON events (wall_clock);
    CREATE INDEX events_type_source_idx ON events (event_type, source);

Configuration: ``DATABASE_URL`` (SQLAlchemy URL). Buffered writes flush in
batches; ``flush``/``close`` force a final write.

"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    insert,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect, Engine
from sqlalchemy.types import TypeDecorator

from ..event import Event
from ..sink import BaseSink


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-preserving datetime, stored as ISO text on SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(String(40))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("wall_clock must be timezone-aware")
        normalized = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return normalized.isoformat(timespec="microseconds")
        return normalized

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


metadata = MetaData()

events_table = Table(
    "events",
    metadata,
    Column(
        "id",
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    ),
    Column("event_type", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("wall_clock", UTCDateTime(), nullable=False),
    Column("monotonic_ns", BigInteger, nullable=False),
    Column("level", Text, nullable=False),
    Column("message", Text, nullable=False, default="", server_default=text("''")),
    Column("duration_ns", BigInteger, nullable=False, default=-1, server_default=text("-1")),
    Column(
        "payload",
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    ),
)
Index("events_wall_clock_idx", events_table.c.wall_clock)
Index("events_type_source_idx", events_table.c.event_type, events_table.c.source)


class DatabaseSink(BaseSink):
    """Write events to a Superset-backed SQL database.

    Args:
        database_url: SQLAlchemy URL. Defaults to ``DATABASE_URL`` env var.
        batch_size: Number of events buffered before an automatic flush.
    """

    def __init__(self, database_url: str = "", batch_size: int = 50) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL", "")
        if not self.database_url:
            raise ValueError("database_url or DATABASE_URL is required")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self.batch_size = batch_size
        self.engine: Engine = create_engine(self.database_url)
        metadata.create_all(self.engine)
        self._buffer: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._closed = False

    def emit(self, event: Event) -> None:
        event.validate()
        row = {
            "event_type": event.event_type,
            "source": event.source,
            "wall_clock": event.wall_clock,
            "monotonic_ns": event.monotonic_ns,
            "level": event.level.value,
            "message": event.message,
            "duration_ns": event.duration_ns,
            "payload": json.loads(json.dumps(event.payload, allow_nan=False)),
        }
        with self._lock:
            if self._closed:
                raise RuntimeError("cannot emit to a closed DatabaseSink")
            self._buffer.append(row)
            if len(self._buffer) >= self.batch_size:
                self._flush_locked()

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        pending = list(self._buffer)
        try:
            with self.engine.begin() as connection:
                connection.execute(insert(events_table), pending)
        except Exception as exc:  # noqa: BLE001 - telemetry must not fail the run
            print(f"[rfs] WARNING: database flush failed ({exc}); will retry later")
            return
        del self._buffer[: len(pending)]

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._flush_locked()
            if self._buffer:
                print(f"[rfs] WARNING: dropping {len(self._buffer)} unpersisted event(s)")
                self._buffer.clear()
            self.engine.dispose()
            self._closed = True
