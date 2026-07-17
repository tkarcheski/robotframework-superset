"""Base class for Robot Framework Listener API v3 listeners.

Provides the plumbing every RF listener needs so subclasses only translate
domain events into :class:`~robotframework_superset.event.Event` objects and
hand them to a :class:`~robotframework_superset.sink.Sink`:

- Top-level suite-boundary detection via depth tracking (RF fires
  ``start_suite``/``end_suite`` for every nested suite; the outermost pair is
  usually the one that matters for a "run").
- A single ``_emit`` helper that stamps events with both clocks at the ingest
  boundary and routes them to the configured sink, skip-and-logging on sink
  failure so a broken backend never fails a test.

Subclasses override the ``on_*`` template hooks, not the raw RF API methods.

Concrete listeners register under the ``robotframework_superset.listeners``
entry-point group (see :mod:`robotframework_superset.registry`).
"""

from __future__ import annotations

from typing import Any, Optional

from ..event import Event, EventLevel, monotonic_ns, utc_now
from ..sink import BaseSink, Sink


class _StdoutSink(BaseSink):
    """Default sink: prints each event's one-line dict to stdout.

    Used when no sink is supplied so a freshly-registered listener does
    something visible without configuration.
    """

    def emit(self, event: Event) -> None:  # pragma: no cover - trivial
        print(f"[rfs] {event.to_dict()}")


class BaseListener:
    """Abstract base for Robot Framework v3 listeners.

    Args:
        sink: Destination for emitted events. Defaults to a stdout sink.
        source: Stable id recorded on every event's ``source`` field.
    """

    ROBOT_LISTENER_API_VERSION = 3

    def __init__(self, sink: Optional[Sink] = None, source: str = "robot") -> None:
        self.sink: Sink = sink or _StdoutSink()
        self.source = source
        self._suite_depth = 0

    # ------------------------------------------------------------------
    # Emit helper — the single ingest boundary where clocks are read.
    # ------------------------------------------------------------------

    def _emit(
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
        except Exception as exc:  # noqa: BLE001 - never fail a test on sink error
            print(f"[rfs] WARNING: sink.emit failed ({exc}); event dropped")

    # ------------------------------------------------------------------
    # RF Listener API v3 — depth tracking + dispatch to template hooks.
    # ------------------------------------------------------------------

    def start_suite(self, data: Any, result: Any) -> None:
        self._suite_depth += 1
        if self._suite_depth == 1:
            self.on_run_start(data, result)
        self.on_suite_start(data, result)

    def end_suite(self, data: Any, result: Any) -> None:
        self.on_suite_end(data, result)
        self._suite_depth -= 1
        if self._suite_depth == 0:
            self.on_run_end(data, result)

    def start_test(self, data: Any, result: Any) -> None:
        self.on_test_start(data, result)

    def end_test(self, data: Any, result: Any) -> None:
        self.on_test_end(data, result)

    def log_message(self, message: Any) -> None:
        self.on_log_message(message)

    def close(self) -> None:
        """RF calls this once at the very end of the run."""
        try:
            self.sink.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[rfs] WARNING: sink.close failed ({exc})")

    # ------------------------------------------------------------------
    # Template hooks — override in subclasses.
    # ------------------------------------------------------------------

    def on_run_start(self, data: Any, result: Any) -> None:
        """Top-level suite began (depth == 1)."""

    def on_run_end(self, data: Any, result: Any) -> None:
        """Top-level suite ended (depth == 0)."""

    def on_suite_start(self, data: Any, result: Any) -> None:
        """Any suite began (including nested)."""

    def on_suite_end(self, data: Any, result: Any) -> None:
        """Any suite ended (including nested)."""

    def on_test_start(self, data: Any, result: Any) -> None:
        """A test case began."""

    def on_test_end(self, data: Any, result: Any) -> None:
        """A test case ended."""

    def on_log_message(self, message: Any) -> None:
        """A ``log`` message was emitted during execution."""
