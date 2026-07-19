"""Standard Robot Framework listener — the reference listener implementation.

Translates the full RF Listener API v3 lifecycle (run/suite/test/keyword/log)
into :class:`~robotframework_superset.event.Event` objects and routes them to
a sink. Every event gets both wall-clock and monotonic timestamps at the
ingest boundary; suite, test, and keyword durations are computed from paired
monotonic reads so they are immune to clock steps.

Register on the command line::

    robot --listener robotframework_superset.listeners.robot_listener.RobotFrameworkListener:sink=db tests/

Listener arguments (``key=value``, colon-separated per RF convention):

- ``sink=<name>`` — resolve the sink through the registry (``db``, ``null``,
  ``memory``, ``stdout``, or any external plugin). Default: stdout.
- ``keywords=true`` — also emit ``robot.keyword.start/end`` events. Off by
  default because keyword events are high-volume. Requires RF >= 7 (Listener
  API v3 gained keyword boundaries in 7.0; on RF 6 nothing is emitted).
- ``logs=false`` — suppress ``robot.log`` events.
- Any other ``key=value`` is forwarded to the sink's constructor (e.g.
  ``sink=db:database_url=sqlite:///rfs.db:batch_size=100``).

Emitted event types:
    ``robot.run.start`` / ``robot.run.end`` (totals: total/passed/failed/skipped)
    ``robot.suite.start`` / ``robot.suite.end``
    ``robot.test.start`` / ``robot.test.end`` (status, tags, duration_ns)
    ``robot.keyword.start`` / ``robot.keyword.end`` (duration_ns; opt-in)
    ``robot.log`` (RF level mapped onto :class:`EventLevel`)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..event import EventLevel, elapsed_ns, monotonic_ns
from ..registry import parse_kwargs, resolve_sink
from ..sink import Sink
from .base import BaseListener

# RF log levels → EventLevel. FAIL/ERROR both map to ERROR; HTML and SKIP are
# informational.
_LEVELS: Dict[str, EventLevel] = {
    "TRACE": EventLevel.TRACE,
    "DEBUG": EventLevel.DEBUG,
    "INFO": EventLevel.INFO,
    "HTML": EventLevel.INFO,
    "SKIP": EventLevel.INFO,
    "WARN": EventLevel.WARN,
    "ERROR": EventLevel.ERROR,
    "FAIL": EventLevel.ERROR,
}


def _status_level(status: str) -> EventLevel:
    return EventLevel.ERROR if status == "FAIL" else EventLevel.INFO


class RobotFrameworkListener(BaseListener):
    """Emit a dual-clock event for each RF lifecycle transition.

    Args:
        *args: RF-style ``key=value`` listener arguments (see module docs).
        sink: Programmatic sink override; takes precedence over ``sink=``.
    """

    def __init__(self, *args: str, sink: Optional[Sink] = None) -> None:
        options = parse_kwargs(tuple(args))
        self._emit_keywords = bool(options.pop("keywords", False))
        self._emit_logs = bool(options.pop("logs", True))
        sink_name = options.pop("sink", "")
        if sink is None and sink_name:
            # Remaining options are the sink's constructor kwargs.
            sink = resolve_sink(str(sink_name), **options)
        super().__init__(sink=sink)
        self._run_start_ns = 0
        self._suite_start_ns: List[int] = []
        self._test_start_ns = 0
        self._kw_start_ns: List[int] = []

    # ------------------------------------------------------------------
    # Run boundaries (outermost suite pair).
    # ------------------------------------------------------------------

    def on_run_start(self, data: Any, result: Any) -> None:
        self._run_start_ns = monotonic_ns()
        self._emit(
            "robot.run.start",
            message=data.name,
            name=data.name,
            source=str(data.source or ""),
            test_count=data.test_count,
        )

    def on_run_end(self, data: Any, result: Any) -> None:
        stats = result.statistics
        self._emit(
            "robot.run.end",
            message=f"{data.name}: {result.status}",
            level=_status_level(result.status),
            duration_ns=elapsed_ns(self._run_start_ns),
            name=data.name,
            status=result.status,
            total=stats.total,
            passed=stats.passed,
            failed=stats.failed,
            skipped=stats.skipped,
        )

    # ------------------------------------------------------------------
    # Suites (every level, including the outermost).
    # ------------------------------------------------------------------

    def on_suite_start(self, data: Any, result: Any) -> None:
        self._suite_start_ns.append(monotonic_ns())
        self._emit(
            "robot.suite.start",
            message=data.longname,
            name=data.name,
            longname=data.longname,
            source=str(data.source or ""),
            test_count=data.test_count,
        )

    def on_suite_end(self, data: Any, result: Any) -> None:
        start = self._suite_start_ns.pop() if self._suite_start_ns else monotonic_ns()
        self._emit(
            "robot.suite.end",
            message=f"{data.longname}: {result.status}",
            level=_status_level(result.status),
            duration_ns=elapsed_ns(start),
            name=data.name,
            longname=data.longname,
            status=result.status,
        )

    # ------------------------------------------------------------------
    # Tests.
    # ------------------------------------------------------------------

    def on_test_start(self, data: Any, result: Any) -> None:
        self._test_start_ns = monotonic_ns()
        self._emit(
            "robot.test.start",
            message=data.longname,
            name=data.name,
            longname=data.longname,
            tags=[str(t) for t in data.tags],
        )

    def on_test_end(self, data: Any, result: Any) -> None:
        self._emit(
            "robot.test.end",
            message=f"{data.longname}: {result.status}",
            level=_status_level(result.status),
            duration_ns=elapsed_ns(self._test_start_ns),
            name=data.name,
            longname=data.longname,
            status=result.status,
            tags=[str(t) for t in data.tags],
            error=result.message,
        )

    # ------------------------------------------------------------------
    # Keywords (opt-in; RF >= 7).
    # ------------------------------------------------------------------

    def on_keyword_start(self, data: Any, result: Any) -> None:
        if not self._emit_keywords:
            return
        self._kw_start_ns.append(monotonic_ns())
        self._emit(
            "robot.keyword.start",
            message=getattr(data, "full_name", data.name),
            name=getattr(data, "full_name", data.name),
            type=str(getattr(data, "type", "KEYWORD")),
        )

    def on_keyword_end(self, data: Any, result: Any) -> None:
        if not self._emit_keywords:
            return
        start = self._kw_start_ns.pop() if self._kw_start_ns else monotonic_ns()
        self._emit(
            "robot.keyword.end",
            message=f"{getattr(data, 'full_name', data.name)}: {result.status}",
            level=_status_level(result.status),
            duration_ns=elapsed_ns(start),
            name=getattr(data, "full_name", data.name),
            type=str(getattr(data, "type", "KEYWORD")),
            status=result.status,
        )

    # ------------------------------------------------------------------
    # Log messages.
    # ------------------------------------------------------------------

    def on_log_message(self, message: Any) -> None:
        if not self._emit_logs:
            return
        self._emit(
            "robot.log",
            message=message.message,
            level=_LEVELS.get(message.level, EventLevel.INFO),
            robot_level=message.level,
        )
