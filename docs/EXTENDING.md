# Extending robotframework-superset

The framework is built to be extended without forking. A third party adds a
**sink**, a **feed**, or a **listener** by subclassing a base class and
registering an [entry point](https://packaging.python.org/en/latest/specifications/entry-points/);
the [registry](../src/robotframework_superset/registry.py) discovers it at
runtime. Built-in components register the same way and are indistinguishable
from external plugins.

Read [ARCHITECTURE.md](ARCHITECTURE.md) first for the abstractions this guide
builds on, and [TIMESTAMPS.md](TIMESTAMPS.md) for the dual-clock contract every
producer must honor.

## 1. Which extension point?

| Extend a… | When the goal is to…                                              | Base class     |
|-----------|------------------------------------------------------------------|----------------|
| **Sink**  | send events somewhere new (a file, a queue, GELF, an HTTP API)   | `BaseSink`     |
| **Feed**  | instrument non-RF activity (an HTTP call, a socket, a queue read)| `BaseFeed`     |
| **Listener** | react to the Robot Framework run lifecycle                    | `BaseListener` |

All three import from the package root:

```python
from robotframework_superset import BaseSink, BaseFeed, BaseListener, Event, EventLevel
```

## 2. Writing a sink

A sink is the destination side of the pipeline. The
[protocol](../src/robotframework_superset/sink.py) is four methods; `BaseSink`
implements `emit_many` (in terms of `emit`) and gives `flush`/`close` no-op
defaults, so a minimal sink overrides only `emit`.

```python
from robotframework_superset import BaseSink, Event


class StdoutSink(BaseSink):
    def emit(self, event: Event) -> None:
        print(event.to_dict())
```

Two rules are non-negotiable (see [ARCHITECTURE §3](ARCHITECTURE.md#3-sinks)):

1. **Persist both clocks.** `event.to_dict()` already serializes `wall_clock`
   and `monotonic_ns`; a columnar sink reads the fields directly. Never drop
   either.
2. **Skip-and-log, never raise.** `emit` must not raise on a transient backend
   failure — one bad event must never abort a test run. Reserve exceptions for
   programmer error. Buffer, then guard the write:

```python
class BufferedSink(BaseSink):
    def __init__(self, batch_size: int = 50) -> None:
        self._buffer: list[Event] = []
        self._batch_size = batch_size

    def emit(self, event: Event) -> None:
        self._buffer.append(event)
        if len(self._buffer) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        try:
            self._write(self._buffer)          # your backend call
        except Exception as exc:               # skip-and-log
            print(f"[rfs] WARNING: flush failed ({exc}); dropped {len(self._buffer)}")
        self._buffer.clear()

    def close(self) -> None:
        self.flush()                           # never lose the last batch
```

The complete, runnable version of this pattern ships in
[`examples/jsonl_sink_plugin/`](../examples/jsonl_sink_plugin/) — see §6.

## 3. Writing a feed

A feed wraps activity Robot Framework does not push — an HTTP call, a socket
read. Subclass `BaseFeed`, set a stable `source`, and use `record()` to measure
around a call so `duration_ns` is accurate (see
[TIMESTAMPS §3](TIMESTAMPS.md#3-getting-an-accurate-duration-for-free)):

```python
from robotframework_superset import BaseFeed


class WeatherFeed(BaseFeed):
    def __init__(self, sink=None) -> None:
        super().__init__(sink=sink, source="weather")

    def fetch(self, city: str) -> int:
        with self.record("weather.fetch", city=city) as rec:
            temp_c = self._http_get(city)      # the measured work
            rec["temp_c"] = temp_c             # merged into the event payload
        return temp_c
```

`record()` yields a mutable dict; anything added to it is merged into the
emitted event's `payload`, and `duration_ns` is the monotonic delta across the
block. For point-in-time events (no duration), call `emit()` directly:

```python
self.emit("weather.error", level=EventLevel.ERROR, message=str(exc), city=city)
```

Keep secrets out of payloads — record variable *names* or booleans (e.g.
`{"api_key_present": True}`), never values.

## 4. Writing a listener

A listener consumes the Robot Framework **Listener API v3** lifecycle.
`BaseListener` tracks suite depth (so `on_run_start`/`on_run_end` fire only for
the outermost suite) and provides `_emit`, the single ingest boundary that
stamps both clocks and routes to the sink with skip-and-log. Override the
`on_*` template hooks — not the raw RF API methods.

```python
from robotframework_superset import BaseListener, EventLevel
from robotframework_superset.event import monotonic_ns


class TestTimingListener(BaseListener):
    def __init__(self, sink=None) -> None:
        super().__init__(sink=sink, source="timing")
        self._starts: dict[str, int] = {}

    def on_test_start(self, data, result) -> None:
        self._starts[data.name] = monotonic_ns()      # pair the monotonic read
        self._emit("timing.test.start", message=data.name)

    def on_test_end(self, data, result) -> None:
        start = self._starts.pop(data.name, monotonic_ns())
        self._emit(
            "timing.test.end",
            message=data.name,
            level=EventLevel.INFO,
            duration_ns=monotonic_ns() - start,       # step-immune duration
            status=result.status,
        )
```

Available hooks: `on_run_start`, `on_run_end`, `on_suite_start`,
`on_suite_end`, `on_test_start`, `on_test_end`, `on_log_message`. The base
class calls `sink.close()` when RF ends the run, so buffered sinks flush
automatically.

Attach a registered listener on the Robot command line by its import path:

```bash
robot --listener my_pkg.timing:TestTimingListener tests/
```

## 5. Registering the plugin

Discovery is by entry point. Declare the class under the matching group in the
plugin's `pyproject.toml`:

```toml
[project.entry-points."robotframework_superset.sinks"]
jsonl = "my_pkg.jsonl_sink:JsonlSink"

[project.entry-points."robotframework_superset.feeds"]
weather = "my_pkg.weather:WeatherFeed"

[project.entry-points."robotframework_superset.listeners"]
timing = "my_pkg.timing:TestTimingListener"
```

The groups are:

- `robotframework_superset.listeners`
- `robotframework_superset.feeds`
- `robotframework_superset.sinks`

After installing the plugin (`pip install .`), the registry finds it by name:

```python
from robotframework_superset.registry import available, load_sink

available()                       # {"listeners": [...], "feeds": [...], "sinks": ["jsonl", ...]}
sink = load_sink("jsonl", path="events.jsonl")
```

`load_sink`, `load_feed`, and `load_listener` forward extra arguments to the
constructor. An unknown name raises `KeyError` listing what is available.

## 6. A complete example plugin

[`examples/jsonl_sink_plugin/`](../examples/jsonl_sink_plugin/) is a full,
dependency-free sink plugin — a worked version of everything above:

- [`rfs_jsonl_sink.py`](../examples/jsonl_sink_plugin/rfs_jsonl_sink.py)
  implements `JsonlSink` (buffered, skip-and-log, both clocks persisted via
  `event.to_dict()`).
- [`pyproject.toml`](../examples/jsonl_sink_plugin/pyproject.toml) registers it
  under `robotframework_superset.sinks` as `jsonl`.

It runs with no install:

```bash
python examples/jsonl_sink_plugin/rfs_jsonl_sink.py   # writes events.jsonl
```

or installs as a discoverable plugin:

```bash
pip install ./examples/jsonl_sink_plugin
python -c "from robotframework_superset.registry import load_sink; print(load_sink('jsonl'))"
```

The test at [`tests/test_examples.py`](../tests/test_examples.py) loads and
exercises the example end-to-end, so CI proves it imports and runs.

## 7. Testing an extension

Use `MemorySink` to assert on emitted events without any backend:

```python
from robotframework_superset.sinks.null import MemorySink

def test_weather_feed_records_duration() -> None:
    sink = MemorySink()
    WeatherFeed(sink=sink).fetch("Detroit")
    (event,) = sink.events
    assert event.event_type == "weather.fetch"
    assert event.duration_ns >= 0            # measured, not the -1 default
    assert event.payload["city"] == "Detroit"
```

For a sink, assert both clocks survive the round trip — that is the invariant
most worth a regression test:

```python
def test_sink_persists_both_clocks() -> None:
    ...   # emit, read back, assert wall_clock and monotonic_ns are present
```

## See also

- [ARCHITECTURE.md](ARCHITECTURE.md) — the abstractions and their contracts.
- [TIMESTAMPS.md](TIMESTAMPS.md) — the dual-clock model in depth.
- [COMPONENTS.md](COMPONENTS.md) — configuring the built-in components.
- [CONTRIBUTING.md](../CONTRIBUTING.md) — dev setup and the CI gates.
