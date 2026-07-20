"""Tests for the GELF sink, its vendored transport, and the MultiSink composite.

The GELF endpoint is mocked with an in-process TCP server that records the
NUL-terminated frames it receives, so the tests assert on the exact wire format
without a real Graylog.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from datetime import datetime, timezone

from robotframework_superset.event import Event, EventLevel
from robotframework_superset.sink import Sink
from robotframework_superset.sinks.gelf import GelfSink
from robotframework_superset.sinks.multi import MultiSink
from robotframework_superset.sinks.null import MemorySink


class MockGelfServer:
    """Single-connection TCP server that collects NUL-terminated GELF frames."""

    def __init__(self) -> None:
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(1)
        self.host, self.port = self._srv.getsockname()
        self.frames: list[bytes] = []
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self._srv.accept()
        except OSError:
            return
        with conn:
            buf = b""
            while True:
                try:
                    chunk = conn.recv(4096)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\x00" in buf:
                    frame, buf = buf.split(b"\x00", 1)
                    self.frames.append(frame)

    def wait_for_frame(self, timeout: float = 5.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.frames:
                return json.loads(self.frames[0].decode("utf-8"))
            time.sleep(0.01)
        raise AssertionError("no GELF frame received within timeout")

    def close(self) -> None:
        try:
            self._srv.close()
        except OSError:
            pass


def _event() -> Event:
    return Event(
        event_type="robot.test.end",
        source="robot",
        wall_clock=datetime(2026, 7, 17, 12, 34, 56, 123456, tzinfo=timezone.utc),
        monotonic_ns=1234567890,
        level=EventLevel.ERROR,
        message="login test failed",
        duration_ns=42_000_000,
        payload={"status": "FAIL", "suite": "auth", "retries": None},
    )


def test_gelf_sink_ships_frame_with_both_timestamps() -> None:
    server = MockGelfServer()
    event = _event()
    sink = GelfSink(host=server.host, port=server.port, source="test-host")
    try:
        sink.emit(event)
        sink.close()
        frame = server.wait_for_frame()
    finally:
        server.close()

    assert frame["version"] == "1.1"
    assert frame["host"] == "test-host"
    assert frame["short_message"] == "login test failed"
    # wall_clock -> both GELF timestamp (epoch) and _wall_clock (ISO) preserved.
    assert frame["timestamp"] == event.wall_clock.timestamp()
    assert frame["_wall_clock"] == event.wall_clock.isoformat()
    # monotonic_ns preserved exactly.
    assert frame["_monotonic_ns"] == event.monotonic_ns
    # ERROR -> syslog 3.
    assert frame["level"] == 3
    assert frame["_level_name"] == "ERROR"
    assert frame["_event_type"] == "robot.test.end"
    assert frame["_source"] == "robot"
    assert frame["_duration_ns"] == 42_000_000


def test_gelf_sink_flattens_payload_and_drops_none() -> None:
    server = MockGelfServer()
    sink = GelfSink(host=server.host, port=server.port, source="test-host")
    try:
        sink.emit(_event())
        sink.close()
        frame = server.wait_for_frame()
    finally:
        server.close()

    assert frame["_status"] == "FAIL"
    assert frame["_suite"] == "auth"
    assert "_retries" not in frame  # None values are dropped


def test_gelf_sink_short_message_falls_back_to_event_type() -> None:
    server = MockGelfServer()
    event = Event(event_type="console.rx", source="console", message="")
    sink = GelfSink(host=server.host, port=server.port)
    try:
        sink.emit(event)
        sink.close()
        frame = server.wait_for_frame()
    finally:
        server.close()

    assert frame["short_message"] == "console.rx"


def test_gelf_sink_never_raises_when_endpoint_unreachable() -> None:
    # Nothing is listening on this port; emit must skip-and-log, not raise.
    sink = GelfSink(host="127.0.0.1", port=1, timeout=0.2)
    sink.emit(_event())  # no exception
    sink.close()


def test_gelf_sink_satisfies_protocol() -> None:
    assert isinstance(GelfSink(host="127.0.0.1", port=12201), Sink)


def test_multisink_fans_out_to_db_stand_in_and_gelf() -> None:
    # Proves a single run reaches both a DB-shaped sink and the GELF sink.
    # MemorySink stands in for DatabaseSink (still a skeleton, issue #8).
    server = MockGelfServer()
    db_like = MemorySink()
    gelf = GelfSink(host=server.host, port=server.port, source="test-host")
    multi = MultiSink(db_like, gelf)
    event = _event()
    try:
        multi.emit(event)
        multi.close()
        frame = server.wait_for_frame()
    finally:
        server.close()

    # DB-shaped sink received the event object...
    assert len(db_like.events) == 1
    assert db_like.events[0] is event
    # ...and the GELF endpoint received the serialized frame.
    assert frame["_event_type"] == "robot.test.end"
    assert frame["_monotonic_ns"] == event.monotonic_ns


def test_multisink_isolates_a_failing_sink() -> None:
    class Boom(MemorySink):
        def emit(self, event: Event) -> None:
            raise RuntimeError("backend down")

    good = MemorySink()
    multi = MultiSink(Boom(), good)
    multi.emit(_event())  # Boom's failure must not stop `good` from receiving.
    assert len(good.events) == 1


def test_multisink_satisfies_protocol() -> None:
    assert isinstance(MultiSink(), Sink)
