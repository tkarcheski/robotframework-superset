"""Standard Robot Framework listener — the reference listener implementation.

Translates the full RF Listener API v3 lifecycle (run/suite/test/keyword/log)
into :class:`~robotframework_superset.event.Event` objects and routes them to
a sink. Every event gets both wall-clock and monotonic timestamps at the
ingest boundary; keyword and test durations are computed from paired
monotonic reads so they are immune to clock steps.

Register on the command line::

    robot --listener robotframework_superset.listeners.robot_listener.RobotFrameworkListener tests/

Sink selection (a later increment; see the tracking issue) is via listener
arguments, e.g. ``...:sink=db`` resolved through the registry.

STATUS: interface skeleton. Method bodies raise NotImplementedError until the
"standard Robot Framework listener" issue is implemented.
"""

from __future__ import annotations

from typing import Any

from .base import BaseListener


class RobotFrameworkListener(BaseListener):
    """Emit an event for each RF lifecycle transition.

    Planned event types:
        ``robot.run.start`` / ``robot.run.end``
        ``robot.suite.start`` / ``robot.suite.end``
        ``robot.test.start`` / ``robot.test.end`` (with status + duration_ns)
        ``robot.keyword.start`` / ``robot.keyword.end`` (with duration_ns)
        ``robot.log`` (level-mapped)
    """

    def on_run_start(self, data: Any, result: Any) -> None:
        raise NotImplementedError

    def on_run_end(self, data: Any, result: Any) -> None:
        raise NotImplementedError

    def on_test_start(self, data: Any, result: Any) -> None:
        raise NotImplementedError

    def on_test_end(self, data: Any, result: Any) -> None:
        raise NotImplementedError
