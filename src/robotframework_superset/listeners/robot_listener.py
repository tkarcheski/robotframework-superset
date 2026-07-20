"""Standard Robot Framework listener — the reference listener implementation.

Translates the full RF Listener API v3 lifecycle (run/suite/test/keyword/log)
into :class:`~robotframework_superset.event.Event` objects and routes them to
a sink. Every event gets both wall-clock and monotonic timestamps at the
ingest boundary; keyword and test durations are computed from paired
monotonic reads so they are immune to clock steps.

Register on the command line::

    robot --listener robotframework_superset.listeners.robot_listener.RobotFrameworkListener tests/

Sink selection uses ``key=value`` listener arguments, e.g. ``...:sink=db``
resolved through the entry-point registry. Use ``keyword_events=false`` to
disable the high-volume keyword callbacks.

"""

from __future__ import annotations

from typing import Any, Hashable

from ..event import EventLevel, duration_since, monotonic_ns, utc_now
from ..sink import Sink
from .base import BaseListener, parse_listener_arguments


def _identity(value: Any) -> Hashable:
    identifier = getattr(value, "id", None)
    return str(identifier) if identifier is not None else id(value)


def _string(value: Any, name: str, default: str = "") -> str:
    item = getattr(value, name, default)
    return default if item is None else str(item)


def _sequence(value: Any, name: str) -> list[str]:
    item = getattr(value, name, ()) or ()
    return [str(part) for part in item]


def _test_count(value: Any) -> int:
    count = getattr(value, "test_count", None)
    if count is not None:
        return int(count)
    return len(getattr(value, "tests", ()) or ())


def _as_bool(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Expected a boolean value, got {value!r}")


def _level(value: Any) -> EventLevel:
    normalized = str(value or "INFO").upper()
    if normalized == "WARNING":
        normalized = "WARN"
    try:
        return EventLevel(normalized)
    except ValueError:
        return EventLevel.INFO


def _run_totals(result: Any) -> tuple[int, int, int]:
    """Extract pass/fail/skip totals from either statistics or suite results."""
    passed = failed = skipped = 0
    tests = getattr(result, "tests", ()) or ()
    suites = getattr(result, "suites", ()) or ()
    for test in tests:
        status = _string(test, "status").upper()
        if status == "PASS":
            passed += 1
        elif status == "SKIP":
            skipped += 1
        else:
            failed += 1
    for suite in suites:
        child_passed, child_failed, child_skipped = _run_totals(suite)
        passed += child_passed
        failed += child_failed
        skipped += child_skipped
    if tests or suites:
        return passed, failed, skipped

    total = getattr(getattr(result, "statistics", None), "total", None)
    return (
        int(getattr(total, "passed", 0) or 0),
        int(getattr(total, "failed", 0) or 0),
        int(getattr(total, "skipped", 0) or 0),
    )


class RobotFrameworkListener(BaseListener):
    """Emit an event for each RF lifecycle transition.

    Event types:
        ``robot.run.start`` / ``robot.run.end``
        ``robot.suite.start`` / ``robot.suite.end``
        ``robot.test.start`` / ``robot.test.end`` (with status + duration_ns)
        ``robot.keyword.start`` / ``robot.keyword.end`` (with duration_ns)
        ``robot.log`` (level-mapped)
    """

    def __init__(
        self,
        *arguments: str,
        sink: Sink | str | None = None,
        source: str = "robot",
        keyword_events: bool | str = True,
    ) -> None:
        options = parse_listener_arguments(arguments)
        unknown = set(options) - {"sink", "source", "keyword_events"}
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ValueError(f"Unknown listener argument(s): {names}")
        selected_sink: Sink | str | None = options.get("sink", sink)
        selected_source = options.get("source", source)
        selected_keywords: str | bool = options.get("keyword_events", keyword_events)
        super().__init__(sink=selected_sink, source=selected_source)
        self.keyword_events = _as_bool(selected_keywords)
        self._run_started_ns: int | None = None
        self._suite_started_ns: dict[Hashable, int] = {}
        self._test_started_ns: dict[Hashable, int] = {}
        self._keyword_started_ns: dict[Hashable, int] = {}

    def on_run_start(self, data: Any, result: Any) -> None:
        captured_wall = utc_now()
        captured_ns = monotonic_ns()
        self._run_started_ns = captured_ns
        self._emit(
            "robot.run.start",
            message=_string(data, "name"),
            _wall_clock=captured_wall,
            _monotonic_ns=captured_ns,
            name=_string(data, "name"),
            longname=_string(data, "longname"),
            source=_string(data, "source"),
            test_count=_test_count(data),
        )

    def on_run_end(self, data: Any, result: Any) -> None:
        captured_wall = utc_now()
        captured_ns = monotonic_ns()
        passed, failed, skipped = _run_totals(result)
        duration = (
            duration_since(self._run_started_ns, captured_ns)
            if self._run_started_ns is not None
            else -1
        )
        self._emit(
            "robot.run.end",
            message=_string(result, "message"),
            level=EventLevel.ERROR if failed else EventLevel.INFO,
            duration_ns=duration,
            _wall_clock=captured_wall,
            _monotonic_ns=captured_ns,
            name=_string(data, "name"),
            longname=_string(data, "longname"),
            passed=passed,
            failed=failed,
            skipped=skipped,
        )
        self._run_started_ns = None

    def on_suite_start(self, data: Any, result: Any) -> None:
        captured_wall = utc_now()
        captured_ns = monotonic_ns()
        self._suite_started_ns[_identity(data)] = captured_ns
        self._emit(
            "robot.suite.start",
            message=_string(data, "name"),
            _wall_clock=captured_wall,
            _monotonic_ns=captured_ns,
            name=_string(data, "name"),
            longname=_string(data, "longname"),
            source=_string(data, "source"),
            test_count=_test_count(data),
        )

    def on_suite_end(self, data: Any, result: Any) -> None:
        captured_wall = utc_now()
        captured_ns = monotonic_ns()
        started = self._suite_started_ns.pop(_identity(data), None)
        status = _string(result, "status")
        self._emit(
            "robot.suite.end",
            message=_string(result, "message"),
            level=EventLevel.ERROR if status == "FAIL" else EventLevel.INFO,
            duration_ns=duration_since(started, captured_ns) if started is not None else -1,
            _wall_clock=captured_wall,
            _monotonic_ns=captured_ns,
            name=_string(data, "name"),
            longname=_string(data, "longname"),
            status=status,
            test_count=_test_count(data),
        )

    def on_test_start(self, data: Any, result: Any) -> None:
        captured_wall = utc_now()
        captured_ns = monotonic_ns()
        self._test_started_ns[_identity(data)] = captured_ns
        self._emit(
            "robot.test.start",
            message=_string(data, "name"),
            _wall_clock=captured_wall,
            _monotonic_ns=captured_ns,
            name=_string(data, "name"),
            longname=_string(data, "longname"),
            source=_string(data, "source"),
            line=int(getattr(data, "lineno", 0) or 0),
            tags=_sequence(data, "tags"),
        )

    def on_test_end(self, data: Any, result: Any) -> None:
        captured_wall = utc_now()
        captured_ns = monotonic_ns()
        started = self._test_started_ns.pop(_identity(data), None)
        status = _string(result, "status")
        self._emit(
            "robot.test.end",
            message=_string(result, "message"),
            level=EventLevel.ERROR if status == "FAIL" else EventLevel.INFO,
            duration_ns=duration_since(started, captured_ns) if started is not None else -1,
            _wall_clock=captured_wall,
            _monotonic_ns=captured_ns,
            name=_string(data, "name"),
            longname=_string(data, "longname"),
            status=status,
            tags=_sequence(data, "tags"),
        )

    def on_keyword_start(self, data: Any, result: Any) -> None:
        if not self.keyword_events:
            return
        captured_wall = utc_now()
        captured_ns = monotonic_ns()
        self._keyword_started_ns[_identity(data)] = captured_ns
        self._emit(
            "robot.keyword.start",
            message=_string(data, "name"),
            _wall_clock=captured_wall,
            _monotonic_ns=captured_ns,
            name=_string(data, "name"),
            type=_string(data, "type", "KEYWORD"),
            owner=_string(data, "owner"),
        )

    def on_keyword_end(self, data: Any, result: Any) -> None:
        if not self.keyword_events:
            return
        captured_wall = utc_now()
        captured_ns = monotonic_ns()
        started = self._keyword_started_ns.pop(_identity(data), None)
        status = _string(result, "status")
        self._emit(
            "robot.keyword.end",
            message=_string(result, "message"),
            level=EventLevel.ERROR if status == "FAIL" else EventLevel.INFO,
            duration_ns=duration_since(started, captured_ns) if started is not None else -1,
            _wall_clock=captured_wall,
            _monotonic_ns=captured_ns,
            name=_string(data, "name"),
            type=_string(data, "type", "KEYWORD"),
            status=status,
        )

    def on_log_message(self, message: Any) -> None:
        self._emit(
            "robot.log",
            message=_string(message, "message", str(message)),
            level=_level(getattr(message, "level", "INFO")),
            html=bool(getattr(message, "html", False)),
        )
