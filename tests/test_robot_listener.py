"""Tests for the standard RobotFrameworkListener (Listener API v3).

Unit tests drive the listener with an in-process ``robot.api.TestSuite`` run
so the events asserted here are exactly what a real ``robot`` invocation
produces — no fakes of the RF data/result model.
"""

from __future__ import annotations

from typing import List

from robot.api import TestSuite as RobotSuite

from robotframework_superset.event import Event, EventLevel
from robotframework_superset.listeners.robot_listener import RobotFrameworkListener
from robotframework_superset.sinks.null import MemorySink


def _run_sample_suite(listener: RobotFrameworkListener) -> None:
    suite = RobotSuite(name="Sample")
    test = suite.tests.create(name="Passes")
    test.body.create_keyword(name="Log", args=["hello from rfs"])
    failing = suite.tests.create(name="Fails")
    failing.body.create_keyword(name="Fail", args=["boom"])
    suite.run(listener=listener, output=None, stdout=None, stderr=None)


def _types(events: List[Event]) -> List[str]:
    return [e.event_type for e in events]


def test_lifecycle_event_sequence() -> None:
    sink = MemorySink()
    _run_sample_suite(RobotFrameworkListener(sink=sink))
    types = _types(sink.events)
    # Run/suite boundaries frame everything; tests are nested inside.
    assert types[0] == "robot.run.start"
    assert types[1] == "robot.suite.start"
    assert types[-2] == "robot.suite.end"
    assert types[-1] == "robot.run.end"
    assert types.count("robot.test.start") == 2
    assert types.count("robot.test.end") == 2
    # The keyword's Log message surfaces as a robot.log event.
    assert any(t == "robot.log" for t in types)


def test_test_end_carries_status_tags_and_duration() -> None:
    sink = MemorySink()
    _run_sample_suite(RobotFrameworkListener(sink=sink))
    ends = [e for e in sink.events if e.event_type == "robot.test.end"]
    by_name = {e.payload["name"]: e for e in ends}
    assert by_name["Passes"].payload["status"] == "PASS"
    assert by_name["Fails"].payload["status"] == "FAIL"
    assert by_name["Fails"].level is EventLevel.ERROR
    for e in ends:
        assert e.duration_ns >= 0  # paired monotonic reads, never wall-clock
        assert isinstance(e.payload["tags"], list)


def test_run_end_carries_totals() -> None:
    sink = MemorySink()
    _run_sample_suite(RobotFrameworkListener(sink=sink))
    run_end = [e for e in sink.events if e.event_type == "robot.run.end"][0]
    assert run_end.payload["total"] == 2
    assert run_end.payload["passed"] == 1
    assert run_end.payload["failed"] == 1
    assert run_end.payload["skipped"] == 0
    assert run_end.duration_ns >= 0


def test_keyword_events_off_by_default_on_when_enabled() -> None:
    sink_off = MemorySink()
    _run_sample_suite(RobotFrameworkListener(sink=sink_off))
    assert not any(t.startswith("robot.keyword.") for t in _types(sink_off.events))

    sink_on = MemorySink()
    _run_sample_suite(RobotFrameworkListener("keywords=true", sink=sink_on))
    kw_types = [t for t in _types(sink_on.events) if t.startswith("robot.keyword.")]
    assert "robot.keyword.start" in kw_types
    assert "robot.keyword.end" in kw_types
    kw_end = [e for e in sink_on.events if e.event_type == "robot.keyword.end"][0]
    assert kw_end.duration_ns >= 0
    assert kw_end.payload["name"]


def test_log_levels_are_mapped() -> None:
    sink = MemorySink()
    _run_sample_suite(RobotFrameworkListener(sink=sink))
    logs = [e for e in sink.events if e.event_type == "robot.log"]
    hello = [e for e in logs if "hello from rfs" in e.message][0]
    assert hello.level is EventLevel.INFO
    boom = [e for e in logs if "boom" in e.message]
    assert boom and boom[0].level is EventLevel.ERROR


def test_string_args_resolve_sink_via_registry() -> None:
    # RF passes listener args as strings: ...:sink=memory — the listener
    # resolves the sink through the registry. 'memory' keeps events in-process.
    listener = RobotFrameworkListener("sink=memory")
    _run_sample_suite(listener)
    assert isinstance(listener.sink, MemorySink)
    assert _types(listener.sink.events)[0] == "robot.run.start"


def test_events_validate_clean() -> None:
    sink = MemorySink()
    _run_sample_suite(RobotFrameworkListener("keywords=true", sink=sink))
    for event in sink.events:
        event.validate()  # every payload JSON-serializable, clocks tz-aware
