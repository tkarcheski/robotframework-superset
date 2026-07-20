"""A complete example sink plugin for robotframework-superset.

``JsonlSink`` appends every event to a newline-delimited JSON file
(``.jsonl``). It is deliberately dependency-free so the extension guide has a
plugin that imports and runs without a database, a network service, or any
optional extra.

What this example demonstrates:

- Subclassing :class:`robotframework_superset.BaseSink` and implementing the
  four protocol methods (``emit``, ``flush``, ``close``; ``emit_many`` is
  inherited).
- Honoring the central invariant: ``event.to_dict()`` serializes BOTH clocks,
  so every persisted line carries ``wall_clock`` and ``monotonic_ns``.
- The skip-and-log rule: a backend (here, filesystem) failure never raises out
  of ``emit``/``flush`` and so never aborts a test run.
- Buffered writes flushed in batches, with ``flush``/``close`` forcing a
  final write.

Register it as a plugin by adding an entry point (see ``pyproject.toml`` in
this directory), then load it through the registry::

    from robotframework_superset.registry import load_sink

    sink = load_sink("jsonl", path="events.jsonl")

See ``../../docs/EXTENDING.md`` for the full walkthrough.
"""

from __future__ import annotations

import json
import os
import threading
from typing import List

from robotframework_superset import BaseSink, Event


class JsonlSink(BaseSink):
    """Append events to a newline-delimited JSON file.

    Args:
        path: Destination file. Defaults to the ``RFS_JSONL_PATH`` environment
            variable, then ``events.jsonl`` in the working directory.
        buffer_size: Number of events buffered before an automatic flush.
            A batched write keeps per-event overhead low.
    """

    def __init__(self, path: str = "", buffer_size: int = 100) -> None:
        self.path = path or os.getenv("RFS_JSONL_PATH", "events.jsonl")
        self.buffer_size = max(1, buffer_size)
        self._buffer: List[str] = []
        self._lock = threading.Lock()

    def emit(self, event: Event) -> None:
        """Buffer one event; flush automatically once the batch is full."""
        line = json.dumps(event.to_dict(), separators=(",", ":"))
        with self._lock:
            self._buffer.append(line)
            if len(self._buffer) >= self.buffer_size:
                self._flush_locked()

    def flush(self) -> None:
        """Force any buffered events to disk."""
        with self._lock:
            self._flush_locked()

    def close(self) -> None:
        """Flush the final batch. No file handle is kept open between writes."""
        self.flush()

    def _flush_locked(self) -> None:
        """Write and clear the buffer. Caller must hold ``self._lock``."""
        if not self._buffer:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write("\n".join(self._buffer) + "\n")
        except OSError as exc:  # skip-and-log: telemetry never fails the run
            dropped = len(self._buffer)
            print(f"[rfs] WARNING: JsonlSink write to {self.path!r} failed "
                  f"({exc}); dropped {dropped} events")
        self._buffer.clear()


if __name__ == "__main__":
    # Minimal smoke run: emit a couple of events and print the file.
    demo = JsonlSink(path="events.jsonl", buffer_size=1)
    demo.emit(Event(event_type="demo.start", source="example", message="hello"))
    demo.emit(Event(event_type="demo.end", source="example", duration_ns=1_234_567))
    demo.close()
    with open("events.jsonl", encoding="utf-8") as fh:
        print(fh.read(), end="")
