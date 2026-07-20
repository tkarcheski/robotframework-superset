"""Composite sink that fans one event out to several sinks.

A single Robot run often wants events in more than one place at once — persisted
to Superset for dashboards *and* forwarded to Graylog for live log search.
:class:`MultiSink` wraps any number of sinks and forwards every ``emit`` /
``flush`` / ``close`` to each of them::

    from robotframework_superset.sinks.db import DatabaseSink
    from robotframework_superset.sinks.gelf import GelfSink
    from robotframework_superset.sinks.multi import MultiSink

    sink = MultiSink(
        DatabaseSink(database_url="postgresql://..."),
        GelfSink(host="graylog.local", port=12201),
    )

A failing child sink is isolated (skip-and-log) so the remaining sinks still
receive the event — the same robustness the :class:`Sink` contract requires of
an individual sink.
"""

from __future__ import annotations

import sys

from ..event import Event
from ..sink import BaseSink, Sink


class MultiSink(BaseSink):
    """Fan every event out to each wrapped sink, isolating failures."""

    def __init__(self, *sinks: Sink) -> None:
        self.sinks: tuple[Sink, ...] = tuple(sinks)

    def emit(self, event: Event) -> None:
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception as exc:  # isolate one bad sink from the rest
                sys.stderr.write(f"[multisink] emit via {sink!r} failed: {exc}\n")

    def flush(self) -> None:
        for sink in self.sinks:
            try:
                sink.flush()
            except Exception as exc:
                sys.stderr.write(f"[multisink] flush via {sink!r} failed: {exc}\n")

    def close(self) -> None:
        for sink in self.sinks:
            try:
                sink.close()
            except Exception as exc:
                sys.stderr.write(f"[multisink] close via {sink!r} failed: {exc}\n")
