"""Null, stdout, and in-memory sinks — useful defaults and test doubles."""

from __future__ import annotations

from typing import List

from ..event import Event
from ..sink import BaseSink


class NullSink(BaseSink):
    """Discards every event. A safe default when telemetry is disabled."""

    def emit(self, event: Event) -> None:
        return None


class StdoutSink(BaseSink):
    """Prints each event's one-line dict to stdout.

    The default sink for listeners and feeds when none is supplied, so a
    freshly-attached producer does something visible without configuration.
    """

    def emit(self, event: Event) -> None:
        print(f"[rfs] {event.to_dict()}")


class MemorySink(BaseSink):
    """Keeps events in a list. Intended for tests and assertions."""

    def __init__(self) -> None:
        self.events: List[Event] = []

    def emit(self, event: Event) -> None:
        self.events.append(event)
