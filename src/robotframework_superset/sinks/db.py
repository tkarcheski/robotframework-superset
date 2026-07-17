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

STATUS: interface skeleton. Bodies raise NotImplementedError until the
"DB sink + schema" issue is implemented.
"""

from __future__ import annotations

import os

from ..event import Event
from ..sink import BaseSink


class DatabaseSink(BaseSink):
    """Write events to a Superset-backed SQL database.

    Args:
        database_url: SQLAlchemy URL. Defaults to ``DATABASE_URL`` env var.
        batch_size: Number of events buffered before an automatic flush.
    """

    def __init__(self, database_url: str = "", batch_size: int = 50) -> None:
        self.database_url = database_url or os.getenv("DATABASE_URL", "")
        self.batch_size = batch_size

    def emit(self, event: Event) -> None:
        raise NotImplementedError

    def flush(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
