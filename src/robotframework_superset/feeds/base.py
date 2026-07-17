"""Base class for feeds — non-Robot-Framework event sources.

A *feed* wraps an operation that isn't a Robot Framework lifecycle event but
still produces precisely-timestamped observations: an HTTP call to an LLM
API, a message queue consumer, a file tail, etc. Where a listener is *pushed*
events by RF, a feed *pulls* or *wraps* an external activity.

The canonical pattern is "measure around a call":

    with feed.record("openai.response", model="gpt-4o") as rec:
        resp = client.chat(...)
        rec["tokens"] = resp.usage.total_tokens
    # on exit, an Event is emitted with duration_ns computed from paired
    # monotonic reads and both clocks stamped.

Feeds register under the ``robotframework_superset.feeds`` entry-point group.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from ..event import Event, EventLevel, monotonic_ns, utc_now
from ..sink import BaseSink, Sink


class _StdoutSink(BaseSink):
    def emit(self, event: Event) -> None:  # pragma: no cover - trivial
        print(f"[rfs] {event.to_dict()}")


class BaseFeed:
    """Abstract base for non-RF event sources.

    Args:
        sink: Destination for emitted events.
        source: Stable id recorded on every event's ``source`` field.
    """

    def __init__(self, sink: Optional[Sink] = None, source: str = "feed") -> None:
        self.sink: Sink = sink or _StdoutSink()
        self.source = source

    def emit(
        self,
        event_type: str,
        message: str = "",
        level: EventLevel = EventLevel.INFO,
        duration_ns: int = -1,
        **payload: Any,
    ) -> None:
        """Build a dual-clock event and route it to the sink (skip-and-log)."""
        event = Event(
            event_type=event_type,
            source=self.source,
            wall_clock=utc_now(),
            monotonic_ns=monotonic_ns(),
            level=level,
            message=message,
            duration_ns=duration_ns,
            payload=dict(payload),
        )
        try:
            self.sink.emit(event)
        except Exception as exc:  # noqa: BLE001 - never let telemetry break the app
            print(f"[rfs] WARNING: sink.emit failed ({exc}); event dropped")

    @contextmanager
    def record(self, event_type: str, **payload: Any) -> Iterator[Dict[str, Any]]:
        """Time a block and emit one event with an accurate ``duration_ns``.

        Yields a mutable dict the caller can enrich (e.g. token counts); its
        contents are merged into the emitted event's payload. ``duration_ns``
        is computed from monotonic reads taken at block entry and exit.
        """
        extra: Dict[str, Any] = dict(payload)
        start = monotonic_ns()
        try:
            yield extra
        finally:
            duration = monotonic_ns() - start
            self.emit(event_type, duration_ns=duration, **extra)
