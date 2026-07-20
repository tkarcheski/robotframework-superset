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
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict


_EVENT_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+$")


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


def duration_since(start_ns: int, end_ns: int | None = None) -> int:
    """Return elapsed nanoseconds between two monotonic clock readings.

    ``end_ns`` defaults to a fresh :func:`monotonic_ns` reading. A negative
    delta is rejected because it indicates mixed clocks, values from different
    processes, or otherwise-invalid input.
    """
    if not isinstance(start_ns, int) or isinstance(start_ns, bool) or start_ns < 0:
        raise ValueError("start_ns must be a non-negative integer")
    end = monotonic_ns() if end_ns is None else end_ns
    if not isinstance(end, int) or isinstance(end, bool) or end < start_ns:
        raise ValueError("end_ns must be an integer greater than or equal to start_ns")
    return end - start_ns


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

    def __post_init__(self) -> None:
        """Normalize the wall clock and enforce the event contract."""
        self.validate()

    def validate(self) -> None:
        """Validate fields every producer and sink depends on.

        Validation is intentionally repeatable: sinks call it again before
        persistence so mutations made after construction cannot smuggle an
        invalid payload into a backend.
        """
        if not isinstance(self.wall_clock, datetime):
            raise ValueError("wall_clock must be a datetime")
        if self.wall_clock.tzinfo is None or self.wall_clock.utcoffset() is None:
            raise ValueError("wall_clock must be timezone-aware")
        self.wall_clock = self.wall_clock.astimezone(timezone.utc)
        if not isinstance(self.event_type, str) or not _EVENT_TYPE_RE.fullmatch(self.event_type):
            raise ValueError("event_type must be a lower-case dotted name")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source must be a non-empty string")
        if not isinstance(self.monotonic_ns, int) or isinstance(self.monotonic_ns, bool):
            raise ValueError("monotonic_ns must be an integer")
        if self.monotonic_ns < 0:
            raise ValueError("monotonic_ns must be non-negative")
        if not isinstance(self.level, EventLevel):
            raise ValueError("level must be an EventLevel")
        if not isinstance(self.message, str):
            raise ValueError("message must be a string")
        if not isinstance(self.duration_ns, int) or isinstance(self.duration_ns, bool):
            raise ValueError("duration_ns must be an integer")
        if self.duration_ns < -1:
            raise ValueError("duration_ns must be -1 or a non-negative integer")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a dictionary")
        try:
            json.dumps(self.payload, allow_nan=False)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("payload must be JSON-serializable") from exc

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable representation.

        ``wall_clock`` is rendered as an ISO-8601 string; ``level`` as its
        string value. Sinks that store columnar data should read the fields
        directly rather than round-tripping through this dict.
        """
        self.validate()
        return {
            "event_type": self.event_type,
            "source": self.source,
            "wall_clock": self.wall_clock.isoformat(timespec="microseconds"),
            "monotonic_ns": self.monotonic_ns,
            "level": self.level.value,
            "message": self.message,
            "duration_ns": self.duration_ns,
            "payload": self.payload,
        }
