"""Tests for the console (telnet) listener, against real local sockets."""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable, List, Optional

from robotframework_superset.event import Event
from robotframework_superset.listeners.console import ConsoleListener
from robotframework_superset.sinks.null import MemorySink


def _wait_for(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class _ConsoleServer:
    """Tiny TCP server standing in for a terminal/console server."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.conn: Optional[socket.socket] = None
        self.received = b""
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        self.conn, _ = self.sock.accept()

    def send(self, data: bytes) -> None:
        assert _wait_for(lambda: self.conn is not None), "client never connected"
        assert self.conn is not None
        self.conn.sendall(data)

    def recv_available(self) -> bytes:
        assert self.conn is not None
        self.conn.settimeout(2.0)
        try:
            while True:
                chunk = self.conn.recv(4096)
                if not chunk:
                    break
                self.received += chunk
                if b"\n" in self.received:
                    break
        except socket.timeout:
            pass
        return self.received

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
        self.sock.close()


def _rx(sink: MemorySink) -> List[Event]:
    return [e for e in sink.events if e.event_type == "console.rx"]


def test_client_mode_emits_rx_per_line_with_monotonic_order() -> None:
    server = _ConsoleServer()
    sink = MemorySink()
    listener = ConsoleListener(sink=sink, host="127.0.0.1", port=server.port, channel="dut0")
    listener.on_run_start(None, None)
    try:
        server.send(b"first line\nsecond line\n")
        assert _wait_for(lambda: len(_rx(sink)) >= 2)
        events = _rx(sink)
        assert [e.message for e in events[:2]] == ["first line", "second line"]
        assert events[0].source == "console:dut0"
        stamps = [e.monotonic_ns for e in events]
        assert stamps == sorted(stamps)
        for e in events:
            e.validate()
    finally:
        listener.close()
        server.close()


def test_telnet_iac_negotiation_is_stripped() -> None:
    server = _ConsoleServer()
    sink = MemorySink()
    listener = ConsoleListener(sink=sink, host="127.0.0.1", port=server.port)
    listener.on_run_start(None, None)
    try:
        # IAC DO ECHO + IAC WILL SGA interleaved with real text.
        server.send(b"\xff\xfd\x01hello\xff\xfb\x03 world\n")
        assert _wait_for(lambda: len(_rx(sink)) >= 1)
        assert _rx(sink)[0].message == "hello world"
    finally:
        listener.close()
        server.close()


def test_send_emits_tx_and_reaches_server() -> None:
    server = _ConsoleServer()
    sink = MemorySink()
    listener = ConsoleListener(sink=sink, host="127.0.0.1", port=server.port)
    listener.on_run_start(None, None)
    try:
        assert _wait_for(lambda: server.conn is not None)
        listener.send("reboot")
        assert b"reboot" in server.recv_available()
        tx = [e for e in sink.events if e.event_type == "console.tx"]
        assert tx and tx[0].message == "reboot"
    finally:
        listener.close()
        server.close()


def test_clean_shutdown_no_thread_leak() -> None:
    server = _ConsoleServer()
    sink = MemorySink()
    listener = ConsoleListener(sink=sink, host="127.0.0.1", port=server.port)
    listener.on_run_start(None, None)
    assert _wait_for(lambda: server.conn is not None)
    listener.close()
    server.close()
    thread = listener._reader_thread
    assert thread is None or _wait_for(lambda: not thread.is_alive())


def test_connect_failure_skips_and_logs(capsys: object) -> None:
    sink = MemorySink()
    # Nothing listens on this port; on_run_start must not raise.
    listener = ConsoleListener(sink=sink, host="127.0.0.1", port=1, connect_timeout=0.2)
    listener.on_run_start(None, None)
    listener.close()
    assert _rx(sink) == []


def test_server_mode_accepts_one_session() -> None:
    sink = MemorySink()
    listener = ConsoleListener(sink=sink, mode="server", host="127.0.0.1", port=0)
    listener.on_run_start(None, None)
    try:
        assert listener.bound_port > 0
        client = socket.create_connection(("127.0.0.1", listener.bound_port), timeout=2)
        client.sendall(b"dial-in line\n")
        assert _wait_for(lambda: len(_rx(sink)) >= 1)
        assert _rx(sink)[0].message == "dial-in line"
        client.close()
    finally:
        listener.close()


def test_registry_shares_channel_for_keyword_library() -> None:
    from robotframework_superset.listeners import console as console_mod

    server = _ConsoleServer()
    sink = MemorySink()
    listener = ConsoleListener(sink=sink, host="127.0.0.1", port=server.port, channel="shared0")
    try:
        assert console_mod.get_channel("shared0") is listener
    finally:
        listener.close()
        server.close()
    assert console_mod.get_channel("shared0") is None
