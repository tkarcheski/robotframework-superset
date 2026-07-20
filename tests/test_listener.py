"""Tests for listener argument parsing and Robot lifecycle mapping."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from robot.api import TestSuite as RobotTestSuite

from robotframework_superset.event import EventLevel
from robotframework_superset.listeners.base import parse_listener_arguments
from robotframework_superset.listeners.robot_listener import RobotFrameworkListener
from robotframework_superset.sinks.null import MemorySink


def _data(identifier: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=identifier,
        name=name,
        longname=f"Example.{name}",
        source="example.robot",
        test_count=1,
        tags=["smoke"],
        lineno=3,
        type="KEYWORD",
        owner="BuiltIn",
    )


def test_listener_argument_parser() -> None:
    assert parse_listener_arguments(("sink=null", "keyword-events=false")) == {
        "sink": "null",
        "keyword_events": "false",
    }
    with pytest.raises(ValueError, match="key=value"):
        parse_listener_arguments(("broken",))
    with pytest.raises(ValueError, match="Duplicate"):
        parse_listener_arguments(("sink=null", "sink=db"))


def test_lifecycle_maps_to_ordered_events_with_durations() -> None:
    sink = MemorySink()
    listener = RobotFrameworkListener(sink=sink)
    suite = _data("s1", "Suite")
    test = _data("t1", "Test")
    keyword = _data("k1", "No Operation")
    pending = SimpleNamespace(status="NOT RUN", message="")

    listener.start_suite(suite, pending)
    listener.start_test(test, pending)
    listener.start_keyword(keyword, pending)
    listener.log_message(SimpleNamespace(level="WARN", message="notice", html=False))
    listener.end_keyword(keyword, SimpleNamespace(status="PASS", message=""))
    listener.end_test(test, SimpleNamespace(status="PASS", message=""))
    totals = SimpleNamespace(passed=1, failed=0, skipped=0)
    suite_result = SimpleNamespace(
        status="PASS",
        message="",
        statistics=SimpleNamespace(total=totals),
    )
    listener.end_suite(suite, suite_result)

    assert [event.event_type for event in sink.events] == [
        "robot.run.start",
        "robot.suite.start",
        "robot.test.start",
        "robot.keyword.start",
        "robot.log",
        "robot.keyword.end",
        "robot.test.end",
        "robot.suite.end",
        "robot.run.end",
    ]
    assert sink.events[4].level is EventLevel.WARN
    for event_type in {
        "robot.keyword.end",
        "robot.test.end",
        "robot.suite.end",
        "robot.run.end",
    }:
        event = next(item for item in sink.events if item.event_type == event_type)
        assert event.duration_ns >= 0


def test_keyword_events_can_be_disabled() -> None:
    sink = MemorySink()
    listener = RobotFrameworkListener("keyword_events=false", sink=sink)
    keyword = _data("k1", "No Operation")
    listener.start_keyword(keyword, SimpleNamespace())
    listener.end_keyword(keyword, SimpleNamespace(status="PASS", message=""))
    assert sink.events == []


def test_unknown_listener_argument_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown"):
        RobotFrameworkListener("typo=yes")


def test_real_robot_run_emits_reference_sequence() -> None:
    suite = RobotTestSuite.from_string(
        """*** Test Cases ***
Example
    Log    hello
"""
    )
    sink = MemorySink()
    listener = RobotFrameworkListener(sink=sink, keyword_events=False)
    result = suite.run(listener=listener, output=None, log=None, report=None)

    assert result.return_code == 0
    event_types = [event.event_type for event in sink.events]
    assert event_types == [
        "robot.run.start",
        "robot.suite.start",
        "robot.test.start",
        "robot.log",
        "robot.test.end",
        "robot.suite.end",
        "robot.run.end",
    ]
    assert (
        next(event for event in sink.events if event.event_type == "robot.test.end").payload[
            "status"
        ]
        == "PASS"
    )
    assert (
        next(event for event in sink.events if event.event_type == "robot.run.end").payload[
            "passed"
        ]
        == 1
    )
