"""Gated PostgreSQL integration test for the complete Robot-to-database path."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from robot.api import TestSuite as RobotTestSuite
from sqlalchemy import create_engine, delete, select

from robotframework_superset.listeners.robot_listener import RobotFrameworkListener
from robotframework_superset.sinks.db import DatabaseSink, events_table


@pytest.mark.integration
def test_robot_events_round_trip_through_postgresql() -> None:
    database_url = os.getenv("TEST_DATABASE_URL", "")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not set")

    source = f"integration:{uuid4().hex}"
    sink = DatabaseSink(database_url, batch_size=2)
    listener = RobotFrameworkListener(sink=sink, source=source, keyword_events=False)
    suite = RobotTestSuite.from_string(
        """*** Test Cases ***
Database Round Trip
    Log    persisted
"""
    )
    result = suite.run(listener=listener, output=None, log=None, report=None)
    assert result.return_code == 0

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            rows = list(
                connection.execute(
                    select(events_table)
                    .where(events_table.c.source == source)
                    .order_by(events_table.c.id)
                ).mappings()
            )
            connection.execute(delete(events_table).where(events_table.c.source == source))
    finally:
        engine.dispose()

    assert [row["event_type"] for row in rows] == [
        "robot.run.start",
        "robot.suite.start",
        "robot.test.start",
        "robot.log",
        "robot.test.end",
        "robot.suite.end",
        "robot.run.end",
    ]
    assert all(row["wall_clock"].tzinfo is not None for row in rows)
    assert all(isinstance(row["monotonic_ns"], int) for row in rows)
