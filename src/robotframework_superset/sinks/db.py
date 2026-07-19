"""Superset-backed database sink.

Persists events to a SQL database (PostgreSQL in production, SQLite for local
runs and tests) that Apache Superset reads for dashboards. Both timestamps
are stored on every row — ``wall_clock`` as ``TIMESTAMPTZ`` (ISO-8601 TEXT on
SQLite, offset preserved) and ``monotonic_ns`` as ``BIGINT`` — per the core
invariant.

Schema (created on first write if absent)::

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

One SQLAlchemy code path serves both backends: a type decorator stores
``wall_clock`` as ISO-8601 TEXT on SQLite (offset preserved — SQLite has no
timestamptz) and as a real ``TIMESTAMPTZ`` on PostgreSQL; ``payload`` is
``JSONB`` on PostgreSQL and JSON TEXT on SQLite.

Configuration: ``DATABASE_URL`` (SQLAlchemy URL) — constructor argument or
environment. An unset URL is a hard failure (the sink cannot meaningfully
proceed); everything after construction is skip-and-log, so a flaky database
never fails a test run. Writes are buffered and flushed in batches of
``batch_size``; ``flush``/``close`` force a final write. On a failed flush
the buffer is retained for the next attempt, capped at ``10 * batch_size``
events (oldest dropped, with a warning) so a dead backend cannot grow memory
without bound.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    BigInteger,
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect, Engine
from sqlalchemy.types import JSON, DateTime, TypeDecorator

from ..event import Event
from ..sink import BaseSink


class ISODateTime(TypeDecorator[datetime]):
    """tz-aware timestamp: TIMESTAMPTZ on PostgreSQL, ISO-8601 TEXT on SQLite.

    SQLite has no timezone-aware datetime type; a bare ``DateTime`` column
    silently drops the offset. Storing the ``isoformat()`` string keeps the
    offset intact and still sorts correctly (ISO-8601 is lexicographically
    ordered for a fixed offset).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "sqlite":
            return dialect.type_descriptor(Text())
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: Optional[datetime], dialect: Dialect) -> Any:
        if value is not None and dialect.name == "sqlite":
            return value.isoformat()
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Optional[datetime]:
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        assert value is None or isinstance(value, datetime)
        return value


def build_events_table(metadata: MetaData) -> Table:
    """Define the ``events`` table on ``metadata`` (single schema, both backends)."""
    return Table(
        "events",
        metadata,
        # BIGSERIAL on PostgreSQL; plain INTEGER autoincrement rowid on SQLite.
        Column("id", BigInteger().with_variant(Integer(), "sqlite"), primary_key=True),
        Column("event_type", Text(), nullable=False),
        Column("source", Text(), nullable=False),
        Column("wall_clock", ISODateTime(), nullable=False),
        Column("monotonic_ns", BigInteger(), nullable=False),
        Column("level", Text(), nullable=False),
        Column("message", Text(), nullable=False, default=""),
        Column("duration_ns", BigInteger(), nullable=False, default=-1),
        Column("payload", JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict),
        Index("events_wall_clock_idx", "wall_clock"),
        Index("events_type_source_idx", "event_type", "source"),
    )


class DatabaseSink(BaseSink):
    """Write events to a Superset-backed SQL database.

    Args:
        database_url: SQLAlchemy URL. Defaults to the ``DATABASE_URL`` env
            var; unset is a :class:`ValueError` (hard-fail — see module docs).
        batch_size: Number of events buffered before an automatic flush.
    """

    def __init__(self, database_url: str = "", batch_size: int = 50) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL", "")
        if not self.database_url:
            raise ValueError(
                "DatabaseSink requires a database URL: pass database_url= or set DATABASE_URL"
            )
        self.batch_size = max(1, batch_size)
        self._buffer: List[Dict[str, Any]] = []
        self._engine: Optional[Engine] = None
        self._table: Optional[Table] = None

    # ------------------------------------------------------------------
    # Sink protocol.
    # ------------------------------------------------------------------

    def emit(self, event: Event) -> None:
        """Buffer one event; auto-flush at ``batch_size``. Never raises."""
        try:
            event.validate()
        except ValueError as exc:
            print(f"[rfs] WARNING: invalid event dropped ({exc})")
            return
        self._buffer.append(
            {
                "event_type": event.event_type,
                "source": event.source,
                "wall_clock": event.wall_clock,
                "monotonic_ns": event.monotonic_ns,
                "level": event.level.value,
                "message": event.message,
                "duration_ns": event.duration_ns,
                "payload": event.payload,
            }
        )
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        """Write all buffered events in one transaction. Never raises.

        On failure the buffer is kept for the next flush, truncated to
        ``10 * batch_size`` (oldest first) so a dead backend is bounded.
        """
        if not self._buffer:
            return
        try:
            engine, table = self._connect()
            with engine.begin() as conn:
                conn.execute(table.insert(), self._buffer)
            self._buffer.clear()
        except Exception as exc:  # noqa: BLE001 - skip-and-log per Sink contract
            print(f"[rfs] WARNING: flush of {len(self._buffer)} event(s) failed ({exc}); retained")
            cap = 10 * self.batch_size
            if len(self._buffer) > cap:
                dropped = len(self._buffer) - cap
                del self._buffer[:dropped]
                print(f"[rfs] WARNING: buffer cap {cap} exceeded; dropped {dropped} oldest event(s)")

    def close(self) -> None:
        """Flush the remainder and dispose the engine. Never raises."""
        self.flush()
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    # ------------------------------------------------------------------
    # Lazy connection — first write creates engine + schema.
    # ------------------------------------------------------------------

    def _connect(self) -> tuple[Engine, Table]:
        if self._engine is None or self._table is None:
            engine = create_engine(self.database_url)
            metadata = MetaData()
            table = build_events_table(metadata)
            metadata.create_all(engine)
            self._engine, self._table = engine, table
        return self._engine, self._table
