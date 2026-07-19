"""Core event model with precise, dual-clock timestamps.

Every event captured by any listener or feed carries two independent
timestamps taken at the ingest boundary:

- ``wall_clock`` — a timezone-aware :class:`datetime.datetime` in UTC with
  microsecond precision. Human- and SQL-friendly; use for absolute ordering
  across machines and for display.
- ``monotonic_ns`` — ``time.monotonic_ns()`` at capture. Immune to NTP steps
  and clock skew; use for *durations* and intra-process ordering. Never
  compare monotonic values across processes or hosts.

Rationale: wall-clock time can jump backward (NTP correction, DST, manual
set) which corrupts naive duration math; the monotonic clock cannot, but has
no absolute meaning. Recording both, per event, lets a consumer pick the
right clock for the question being asked. This is the framework's central
invariant — a sink MUST persist both.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict


class EventLevel(str, Enum):
    """Severity of an event, aligned with syslog/RF log levels."""

    TRACE = "TRACE"
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


def utc_now() -> datetime:
    """Return the current wall-clock time as a UTC, tz-aware datetime.

    Microsecond precision is preserved. ``isoformat()`` on the result yields
    an ISO-8601 string with a ``+00:00`` offset (e.g.
    ``2026-07-17T12:34:56.123456+00:00``).
    """
    return datetime.now(timezone.utc)


def monotonic_ns() -> int:
    """Return a monotonic timestamp in integer nanoseconds.

    Thin wrapper over :func:`time.monotonic_ns` so callers and tests share a
    single capture point.
    """
    return time.monotonic_ns()


def elapsed_ns(start_ns: int) -> int:
    """Return nanoseconds elapsed since a ``monotonic_ns()`` start marker.

    The canonical way to compute ``Event.duration_ns``: take a monotonic
    read before the operation, then ``elapsed_ns(start)`` after. Never derive
    durations from wall-clock subtraction — wall clocks can step backward.
    """
    return time.monotonic_ns() - start_ns


@dataclass
class Event:
    """A single precisely-timestamped observation from a listener or feed.

    Attributes:
        event_type: Dotted, source-scoped type, e.g. ``"robot.test.end"``,
            ``"console.rx"``, ``"openai.response"``, ``"ollama.request"``.
        source: The producing component's stable id (listener/feed name),
            e.g. ``"robot"``, ``"console:eth0-console"``, ``"openai"``.
        wall_clock: UTC, tz-aware capture time (microsecond precision).
        monotonic_ns: Monotonic capture time in nanoseconds.
        level: Severity.
        message: Short human-readable summary (one line).
        duration_ns: Optional measured duration in nanoseconds, computed from
            two ``monotonic_ns`` reads (start/end). ``-1`` when not applicable.
        payload: Arbitrary structured detail. MUST be JSON-serializable so
            any sink can persist it. Large blobs belong in an artifact table,
            not here.
    """

    event_type: str
    source: str
    wall_clock: datetime = field(default_factory=utc_now)
    monotonic_ns: int = field(default_factory=monotonic_ns)
    level: EventLevel = EventLevel.INFO
    message: str = ""
    duration_ns: int = -1
    payload: Dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Enforce the event contract; raise :class:`ValueError` on violation.

        Checks (the spec every producer and sink relies on):
        - ``event_type`` and ``source`` are non-empty.
        - ``wall_clock`` is tz-aware (naive datetimes corrupt cross-machine
          ordering; UTC is the convention but any explicit offset is legal).
        - ``payload`` is JSON-serializable, so any sink can persist it.
        """
        if not self.event_type:
            raise ValueError("event_type must be non-empty")
        if not self.source:
            raise ValueError("source must be non-empty")
        if self.wall_clock.tzinfo is None or self.wall_clock.utcoffset() is None:
            raise ValueError("wall_clock must be tz-aware (got a naive datetime)")
        try:
            json.dumps(self.payload)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"payload must be JSON-serializable: {exc}") from exc

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation.

        Validates the event first (see :meth:`validate`) so a malformed
        payload is caught at the producer, not deep inside a sink.
        ``wall_clock`` is rendered as an ISO-8601 string; ``level`` as its
        string value. Sinks that store columnar data should read the fields
        directly rather than round-tripping through this dict.
        """
        self.validate()
        return {
            "event_type": self.event_type,
            "source": self.source,
            "wall_clock": self.wall_clock.isoformat(),
            "monotonic_ns": self.monotonic_ns,
            "level": self.level.value,
            "message": self.message,
            "duration_ns": self.duration_ns,
            "payload": self.payload,
        }
