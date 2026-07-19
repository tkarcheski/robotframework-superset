"""Console listener with telnet support.

Captures raw console I/O as precisely-timestamped events — one event per line
with both clocks stamped at the moment bytes are read, so downstream analysis
can reconstruct exact timing of device output relative to test steps.

Two operating modes (selected by ``mode=``):

- ``client`` (default, a "tap"): opens a TCP/telnet client connection to a
  device's console server (host/port from arguments or
  ``CONSOLE_HOST``/``CONSOLE_PORT``) and mirrors every received line as a
  ``console.rx`` event and every line sent via :meth:`send` as ``console.tx``.
  This is the common lab/CI case — a DUT's serial console exposed over a
  terminal server — and needs no inbound firewall changes.
- ``server``: binds a small TCP server and accepts one session (the DUT or a
  simulator dials in). Same event shape. Bind port 0 to let the OS pick;
  the chosen port is available as :attr:`ConsoleListener.bound_port`.

Timestamp precision: each received chunk is stamped with both clocks
immediately after ``recv`` returns — BEFORE decoding, line-splitting, or any
other parsing — to minimize jitter between byte arrival and timestamp. Lines
split from one chunk share that chunk's stamp (they arrived together).

Telnet: Python's ``telnetlib`` was removed in 3.13, so this module speaks
raw TCP and strips inbound IAC negotiation sequences (IAC + verb + option,
and IAC SB ... IAC SE subnegotiations) without responding to them — a passive
tap does not negotiate. Everything else passes through as console bytes.

Channel registry: each listener registers under its ``channel`` name so a
companion RF keyword library can share the connection (mirror of
rf-graylog's listener/library registry split)::

    from robotframework_superset.listeners.console import get_channel
    get_channel("dut0").send("reboot")

Register on the command line::

    robot --listener robotframework_superset.listeners.console.ConsoleListener:mode=client:host=10.0.0.5:port=2001:channel=dut0 tests/
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Any, Dict, Optional, Union

from ..event import Event, EventLevel, monotonic_ns, utc_now
from ..registry import coerce_value, parse_kwargs, resolve_sink
from ..sink import Sink
from .base import BaseListener

_IAC = 255
_SB = 250
_SE = 240

# Channel-name → live listener, so keyword libraries can share the socket.
_channels: Dict[str, "ConsoleListener"] = {}


def get_channel(name: str) -> Optional["ConsoleListener"]:
    """Return the live listener registered under ``name``, if any."""
    return _channels.get(name)


def _strip_iac(data: bytes) -> bytes:
    """Remove telnet IAC command/negotiation sequences from a byte stream."""
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b != _IAC:
            out.append(b)
            i += 1
            continue
        if i + 1 >= len(data):
            break  # dangling IAC at chunk end; drop
        verb = data[i + 1]
        if verb == _IAC:
            out.append(_IAC)  # escaped 0xFF data byte
            i += 2
        elif verb == _SB:
            end = data.find(bytes((_IAC, _SE)), i + 2)
            i = len(data) if end == -1 else end + 2
        else:
            i += 3  # IAC + verb + option
    return bytes(out)


class ConsoleListener(BaseListener):
    """Mirror telnet/console traffic into the event stream.

    Args:
        *args: RF-style ``key=value`` argument strings (older RF versions).
        sink: Event destination — a :class:`Sink` or a registry name.
        mode: ``"client"`` (tap a remote console server) or ``"server"``.
        host: Console server host (client mode) or bind address (server mode).
            Default ``CONSOLE_HOST`` env or ``127.0.0.1``.
        port: TCP port. Default ``CONSOLE_PORT`` env or ``2323``. In server
            mode, ``0`` binds an ephemeral port (see ``bound_port``).
        channel: Label for this console; ``source`` becomes ``console:<channel>``.
        connect_timeout: Client-mode connection timeout in seconds.
        **options: Forwarded to the named sink's constructor.
    """

    def __init__(
        self,
        *args: str,
        sink: Union[Sink, str, None] = None,
        mode: str = "client",
        host: str = "",
        port: Union[int, str] = -1,
        channel: str = "console",
        connect_timeout: Union[float, str] = 5.0,
        **options: str,
    ) -> None:
        merged: Dict[str, Any] = dict(parse_kwargs(tuple(args)))
        merged.update({k: coerce_value(v) for k, v in options.items()})
        self.mode = str(merged.pop("mode", mode))
        self.host = str(merged.pop("host", host)) or os.getenv("CONSOLE_HOST", "127.0.0.1")
        raw_port = merged.pop("port", port)
        self.port = int(raw_port) if int(raw_port) >= 0 else int(os.getenv("CONSOLE_PORT", "2323"))
        self.channel = str(merged.pop("channel", channel))
        self.connect_timeout = float(merged.pop("connect_timeout", connect_timeout))
        sink_spec = merged.pop("sink", sink)
        resolved: Optional[Sink]
        if isinstance(sink_spec, str):
            resolved = resolve_sink(sink_spec, **merged)
        else:
            resolved = sink_spec
        super().__init__(sink=resolved, source=f"console:{self.channel}")
        self.bound_port = 0
        self._conn: Optional[socket.socket] = None
        self._server_sock: Optional[socket.socket] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        _channels[self.channel] = self

    # ------------------------------------------------------------------
    # Lifecycle.
    # ------------------------------------------------------------------

    def on_run_start(self, data: Any, result: Any) -> None:
        """Open the console channel and start the reader thread. Never raises."""
        try:
            if self.mode == "server":
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((self.host, self.port))
                server.listen(1)
                self.bound_port = server.getsockname()[1]
                self._server_sock = server
            else:
                self._conn = socket.create_connection(
                    (self.host, self.port), timeout=self.connect_timeout
                )
                self._conn.settimeout(0.2)
        except OSError as exc:
            print(f"[rfs] WARNING: console {self.mode} setup {self.host}:{self.port} failed ({exc})")
            return
        self._stop.clear()
        self._reader_thread = threading.Thread(
            target=self._read_loop, name=f"rfs-console-{self.channel}", daemon=True
        )
        self._reader_thread.start()

    def on_run_end(self, data: Any, result: Any) -> None:
        """Stop the reader and close the channel (run boundary)."""
        self._shutdown()

    def close(self) -> None:
        """RF end-of-run hook: drain, close socket, release the channel name."""
        self._shutdown()
        _channels.pop(self.channel, None)
        super().close()

    def send(self, line: str) -> None:
        """Write a line to the console and emit a ``console.tx`` event."""
        with self._lock:
            conn = self._conn
        if conn is None:
            print(f"[rfs] WARNING: console {self.channel} not connected; send dropped")
            return
        try:
            conn.sendall(line.encode() + b"\r\n")
        except OSError as exc:
            print(f"[rfs] WARNING: console send failed ({exc})")
            return
        self._emit("console.tx", message=line, channel=self.channel)

    # ------------------------------------------------------------------
    # Reader thread.
    # ------------------------------------------------------------------

    def _read_loop(self) -> None:
        if self._server_sock is not None:
            self._server_sock.settimeout(0.2)
            while not self._stop.is_set():
                try:
                    session, _ = self._server_sock.accept()
                    session.settimeout(0.2)
                    with self._lock:
                        self._conn = session
                    break
                except socket.timeout:
                    continue
                except OSError:
                    return
        buffer = b""
        while not self._stop.is_set():
            with self._lock:
                conn = self._conn
            if conn is None:
                return
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not chunk:
                return  # peer closed
            # Stamp BOTH clocks now — at byte arrival, before any parsing.
            mono = monotonic_ns()
            wall = utc_now()
            buffer += _strip_iac(chunk)
            while b"\n" in buffer:
                raw_line, buffer = buffer.split(b"\n", 1)
                self._emit_at(mono, wall, raw_line.rstrip(b"\r").decode(errors="replace"))

    def _emit_at(self, mono: int, wall: Any, line: str) -> None:
        """Emit console.rx with the read-time stamps (not emit-time)."""
        event = Event(
            event_type="console.rx",
            source=self.source,
            wall_clock=wall,
            monotonic_ns=mono,
            level=EventLevel.INFO,
            message=line,
            payload={"channel": self.channel},
        )
        try:
            self.sink.emit(event)
        except Exception as exc:  # noqa: BLE001 - never fail a run on sink error
            print(f"[rfs] WARNING: sink.emit failed ({exc}); event dropped")

    def _shutdown(self) -> None:
        self._stop.set()
        thread = self._reader_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except OSError:
                    pass
                self._conn = None
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        self._reader_thread = None
