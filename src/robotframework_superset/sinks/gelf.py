"""GELF/Graylog sink — forwards events to a Graylog GELF-over-TCP input.

This makes GELF "just another sink", aligning with
`rf-graylog <https://github.com/tkarcheski/rf-graylog>`_: rf-graylog splits a
shared ``GelfTcpTransport`` from its listeners; that transport maps onto this
project's :class:`~robotframework_superset.sink.Sink`. Rather than take a
runtime dependency on rf-graylog (a separate, GitLab-hosted project), the small
GELF-over-TCP transport is **vendored** here so this sink is stdlib-only and the
package stays self-contained. See ``docs/ARCHITECTURE.md`` and the PR that
introduced this module for the "vendor vs. depend" rationale.

Event -> GELF 1.1 mapping (per issue #14)::

    wall_clock    -> timestamp        (Unix epoch seconds, microsecond precision)
    wall_clock    -> _wall_clock      (original ISO-8601 string; lossless, keeps tz)
    monotonic_ns  -> _monotonic_ns    (durations / intra-process ordering)
    level         -> level            (numeric syslog/GELF severity)
    message       -> short_message    (falls back to event_type if empty)
    event_type    -> _event_type
    source        -> _source
    duration_ns   -> _duration_ns
    payload{k: v} -> _k: v            (flattened; None values dropped)

Both timestamps are preserved on every frame, honouring the framework's central
invariant. The transport reconnects on failure and never raises for transient
network errors (skip-and-log), per the :class:`Sink` contract.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from ..event import Event, EventLevel
from ..sink import BaseSink

# syslog/GELF numeric severities (lower number = more severe), matching
# rf-graylog's ``robot_graylog_common.levels`` so both projects agree.
_GELF_LEVELS: Dict[str, int] = {
    "EMERGENCY": 0,
    "ALERT": 1,
    "CRITICAL": 2,
    "ERROR": 3,
    "WARN": 4,
    "WARNING": 4,
    "NOTICE": 5,
    "INFO": 6,
    "DEBUG": 7,
    "TRACE": 7,
}

_DEFAULT_GELF_LEVEL = 6  # INFO

# GELF reserves ``_id``; an additional field named ``_id`` is rejected by
# Graylog. Payload keys that would collide are remapped to this instead.
_RESERVED_ID_FIELD = "_payload_id"


def _gelf_level(level: EventLevel) -> int:
    """Map an :class:`EventLevel` to its numeric GELF/syslog severity."""
    return _GELF_LEVELS.get(level.value.upper(), _DEFAULT_GELF_LEVEL)


def _stderr_log(message: str) -> None:
    """Default logger: one line to stderr."""
    sys.stderr.write(message + "\n")


class GelfTcpTransport:
    """Persistent TCP socket that ships GELF frames to a Graylog input.

    Vendored (and lightly adapted) from rf-graylog's
    ``robot_graylog_common.transport`` so this package carries no dependency on
    rf-graylog. Each frame is a JSON object terminated by a NUL byte (``\\0``),
    per the Graylog GELF-over-TCP spec. The socket is opened lazily and
    reconnected on the next frame after any send failure.
    """

    def __init__(
        self,
        host: str,
        port: int,
        timeout: float = 5.0,
        logger: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: Optional[socket.socket] = None
        self._log: Callable[[str], None] = logger or _stderr_log

    def _connect(self) -> None:
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout
            )
        except OSError as exc:
            self._log(f"[gelf] connect failed {self.host}:{self.port} - {exc}")
            self._sock = None

    def send(self, frame: bytes) -> bool:
        """Ship one NUL-terminated frame; reconnect once on failure.

        Returns ``True`` if the frame was handed to the socket, ``False`` if the
        endpoint was unreachable. Never raises for network errors.
        """
        if self._sock is None:
            self._connect()
            if self._sock is None:
                return False
        try:
            self._sock.sendall(frame)
            return True
        except OSError as exc:
            self._log(f"[gelf] send failed: {exc}; will reconnect on next frame")
            try:
                self._sock.close()
            finally:
                self._sock = None
            return False

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


class GelfSink(BaseSink):
    """Forward events to a Graylog GELF-over-TCP input.

    Args:
        host: Graylog GELF-TCP host. Defaults to ``GELF_HOST`` / ``GRAYLOG_HOST``
            env vars, then ``localhost``.
        port: GELF-TCP port. Defaults to ``GELF_PORT`` / ``GRAYLOG_PORT`` env
            vars, then ``12201``.
        source: GELF ``host`` field (the event origin). Defaults to
            ``GELF_SOURCE`` env var, then the local hostname.
        facility: Emitted as ``_facility``. Defaults to
            ``robotframework-superset``.
        timeout: Socket connect/send timeout in seconds.
        transport: Inject a pre-built transport (used by tests). When omitted a
            :class:`GelfTcpTransport` is created from the resolved host/port.
    """

    def __init__(
        self,
        host: str = "",
        port: int = 0,
        source: str = "",
        facility: str = "robotframework-superset",
        timeout: float = 5.0,
        transport: Optional[GelfTcpTransport] = None,
    ) -> None:
        self.host = host or os.getenv("GELF_HOST") or os.getenv("GRAYLOG_HOST") or "localhost"
        self.port = int(
            port or os.getenv("GELF_PORT") or os.getenv("GRAYLOG_PORT") or 12201
        )
        self.source = source or os.getenv("GELF_SOURCE") or socket.gethostname()
        self.facility = facility
        self._transport = transport or GelfTcpTransport(self.host, self.port, timeout)

    @staticmethod
    def _epoch_seconds(when: datetime) -> float:
        """UTC wall-clock as Unix epoch seconds, preserving microseconds."""
        return when.timestamp()

    def _frame(self, event: Event) -> bytes:
        """Serialize ``event.to_dict()`` into a NUL-terminated GELF frame."""
        data = event.to_dict()
        short_message = str(data["message"] or data["event_type"])[:1000]
        gelf: Dict[str, Any] = {
            "version": "1.1",
            "host": self.source,
            "short_message": short_message,
            "timestamp": self._epoch_seconds(event.wall_clock),
            "level": _gelf_level(event.level),
            "_facility": self.facility,
            "_event_type": data["event_type"],
            "_source": data["source"],
            "_level_name": data["level"],
            "_wall_clock": data["wall_clock"],
            "_monotonic_ns": data["monotonic_ns"],
            "_duration_ns": data["duration_ns"],
        }
        payload = data["payload"]
        if isinstance(payload, dict):
            for key, value in payload.items():
                if value is None:
                    continue
                field = f"_{key}"
                if field == "_id":  # reserved by GELF
                    field = _RESERVED_ID_FIELD
                gelf[field] = value
        return json.dumps(gelf, default=str).encode("utf-8") + b"\x00"

    def emit(self, event: Event) -> None:
        """Ship one event; never raises for transient backend failure."""
        try:
            self._transport.send(self._frame(event))
        except Exception as exc:  # skip-and-log so one bad event can't abort a run
            _stderr_log(f"[gelf] dropped event {event.event_type}: {exc}")

    def flush(self) -> None:
        """No-op: GELF frames are shipped immediately, nothing is buffered."""

    def close(self) -> None:
        """Close the underlying TCP transport."""
        self._transport.close()
