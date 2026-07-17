"""Sink protocol — where events go to rest.

A sink is the destination side of the pipeline. Listeners and feeds produce
:class:`~robotframework_superset.event.Event` objects; a sink persists or
forwards them. The reference sink is Superset-backed (PostgreSQL), but the
protocol is deliberately narrow so a sink could also be a file, GELF/Graylog
transport, stdout, or an in-memory buffer for tests.

Design notes:
- ``emit`` should be non-blocking-ish and MUST NOT raise for transient
  backend failures — follow skip-and-log so one bad event never aborts a
  test run. Reserve exceptions for programmer error.
- Sinks MUST persist BOTH timestamps on every event (see
  :mod:`robotframework_superset.event`).
- Batching/buffering is a sink's own concern; ``flush`` forces a write and
  ``close`` releases resources.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable

from .event import Event


@runtime_checkable
class Sink(Protocol):
    """Structural interface every sink implements."""

    def emit(self, event: Event) -> None:
        """Persist or forward a single event. Must not raise on transient error."""
        ...

    def emit_many(self, events: Iterable[Event]) -> None:
        """Persist or forward a batch of events."""
        ...

    def flush(self) -> None:
        """Force any buffered events to the backend."""
        ...

    def close(self) -> None:
        """Flush and release all resources (connections, sockets, files)."""
        ...


class BaseSink:
    """Convenience base implementing ``emit_many`` in terms of ``emit``.

    Subclasses override :meth:`emit` (and usually :meth:`flush`/:meth:`close`).
    """

    def emit(self, event: Event) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def emit_many(self, events: Iterable[Event]) -> None:
        for event in events:
            self.emit(event)

    def flush(self) -> None:
        """No-op by default; buffered sinks override."""

    def close(self) -> None:
        """Flush by default; resource-holding sinks override."""
        self.flush()
