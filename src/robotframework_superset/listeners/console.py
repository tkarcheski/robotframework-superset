"""Console listener with telnet support.

Captures raw console I/O as precisely-timestamped events — one event per line
(or per read chunk) with both clocks stamped at the moment bytes are read, so
downstream analysis can reconstruct exact timing of device output relative to
test steps.

Two operating modes (selected by ``mode=``):

- ``client`` (a "tap"): the listener opens a telnet client connection to a
  device's console server (host/port from ``CONSOLE_HOST``/``CONSOLE_PORT``)
  and mirrors every received line as a ``console.rx`` event, and every line it
  sends as ``console.tx``. This is the common lab/CI case: a DUT exposes its
  serial console over a terminal server speaking telnet.
- ``server``: the listener runs a small telnet server and accepts a session,
  useful when the DUT (or a simulator) dials in. Same event shape.

Design decision (see tracking issue): default to ``client`` mode — tapping an
existing console server is the dominant real-world topology and needs no
inbound firewall changes. ``server`` mode is opt-in.

Timestamp precision: each received line is stamped with ``time.monotonic_ns``
at read time, BEFORE any decoding/parsing, to minimize jitter between the byte
arriving and the timestamp. Wall-clock is captured in the same breath.

Register on the command line::

    robot --listener robotframework_superset.listeners.console.ConsoleListener:mode=client:host=10.0.0.5:port=2001 tests/

STATUS: interface skeleton. Bodies raise NotImplementedError until the
"console listener with telnet support" issue is implemented.
"""

from __future__ import annotations

from typing import Any, Optional

from ..sink import Sink
from .base import BaseListener


class ConsoleListener(BaseListener):
    """Mirror telnet/console traffic into the event stream.

    Args:
        sink: Event destination.
        mode: ``"client"`` (tap a remote console server) or ``"server"``.
        host: Console server host (client mode) or bind address (server mode).
        port: TCP port for the telnet channel.
        channel: Human label for this console (e.g. ``"dut0-console"``), used
            in ``source`` as ``console:<channel>``.
    """

    def __init__(
        self,
        sink: Optional[Sink] = None,
        mode: str = "client",
        host: str = "127.0.0.1",
        port: int = 2323,
        channel: str = "console",
    ) -> None:
        super().__init__(sink=sink, source=f"console:{channel}")
        self.mode = mode
        self.host = host
        self.port = int(port)
        self.channel = channel

    def on_run_start(self, data: Any, result: Any) -> None:
        """Open the telnet channel and start the reader thread."""
        raise NotImplementedError

    def on_run_end(self, data: Any, result: Any) -> None:
        """Stop the reader and close the telnet channel."""
        raise NotImplementedError

    def send(self, line: str) -> None:
        """Write a line to the console and emit a ``console.tx`` event."""
        raise NotImplementedError
