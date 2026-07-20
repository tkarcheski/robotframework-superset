# robotframework-superset

Extensible **listeners** and precisely-timestamped **event feeds** for
[Robot Framework](https://robotframework.org/), visualized with
[Apache Superset](https://superset.apache.org/).

Capture what happens during a test run — Robot lifecycle events, raw
console/telnet traffic, and LLM API calls (OpenAI, Ollama) — as a single
stream of events, each stamped with **two clocks** (wall-clock and monotonic),
persist them to a Superset-backed database, and explore them in dashboards.

> Status: **early scaffold.** The core abstractions (event model, listener /
> feed / sink interfaces, plugin registry) are defined; concrete
> implementations are tracked as issues under the migration epic. See
> [the issue tracker](https://github.com/tkarcheski/robotframework-superset/issues).

## Why two clocks?

Every event records both:

- **`wall_clock`** — UTC, timezone-aware, microsecond precision (ISO-8601).
  Absolute, comparable across machines, good for display and SQL ordering.
- **`monotonic_ns`** — `time.monotonic_ns()`. Never jumps backward (immune to
  NTP steps / DST), so durations computed from it are trustworthy — but it has
  no absolute meaning and can't be compared across processes.

Recording both, per event, at the ingest boundary lets each consumer pick the
right clock for the question. This is the framework's central invariant; a
sink **must** persist both. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Architecture at a glance

```
 producers                         core                     sink            view
┌──────────────────────┐     ┌──────────────┐       ┌───────────────┐   ┌──────────┐
│ RobotFrameworkListener│──┐  │              │       │ DatabaseSink  │   │          │
│ ConsoleListener(telnet)│─┼─▶│    Event     │──────▶│ (PostgreSQL)  │──▶│ Superset │
│ OpenAIFeed            │──┤  │ dual clocks  │       │  or NullSink  │   │dashboards│
│ OllamaFeed            │──┘  │              │       │  or your own  │   │          │
└──────────────────────┘     └──────────────┘       └───────────────┘   └──────────┘
   (listeners + feeds)         Sink protocol            plugins via entry points
```

- **Listeners** are pushed events by Robot Framework (Listener API v3).
- **Feeds** wrap non-RF activity (an HTTP call, a socket) and measure around it.
- **Sinks** persist events; the reference sink is Superset-backed PostgreSQL.
- **Plugins**: listeners, feeds, and sinks are all discovered via entry points,
  so third parties extend the framework without forking it.

## Quickstart

### 1. Install

From a clone (until the package is published to PyPI):

```bash
git clone https://github.com/tkarcheski/robotframework-superset
cd robotframework-superset
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                         # core + ruff/mypy/pytest

python -c "import robotframework_superset as r; print(r.__version__)"   # 0.1.0
```

Optional extras add per-component dependencies: `.[db]` (database sink),
`.[openai]`, `.[ollama]`, or `.[all]`.

### 2. Bring up the stack

The shipped compose file starts PostgreSQL, Redis, and Apache Superset.
Superset reads the same PostgreSQL instance the database sink writes to.

```bash
cp .env.example .env                            # edit credentials first
docker compose -f infra/docker-compose.yml --env-file .env up -d
```

Open http://localhost:8088 and sign in with `SUPERSET_ADMIN_USER` /
`SUPERSET_ADMIN_PASSWORD` from `.env`. Tear the stack down with
`docker compose -f infra/docker-compose.yml --env-file .env down`.

### 3. Emit events

The core library — the event model, the base producers, and the sinks — works
today. Emit precisely-timestamped events and route them to any sink:

```python
from robotframework_superset import BaseFeed
from robotframework_superset.sinks.null import MemorySink

sink = MemorySink()                             # or NullSink, or your own
feed = BaseFeed(sink=sink, source="demo")

with feed.record("demo.work", step="build") as rec:
    rec["ok"] = True                            # your measured work here

event = sink.events[0]
print(event.event_type, event.duration_ns)      # demo.work  <measured ns>
print(event.to_dict())                          # both clocks serialized
```

To persist events to a file instead, use the example
[`JsonlSink`](examples/jsonl_sink_plugin/) — a complete, runnable sink plugin.

### 4. Attach to a Robot run

The standard RF listener and the database sink are landing with their epic
issues (#4 and #8); once installed, a full run streams to Superset:

```bash
# Target flow (see the epic for status):
robot --listener robotframework_superset.listeners.robot_listener.RobotFrameworkListener tests/
```

Until then, a custom listener built on the ready `BaseListener` already runs
end-to-end — see [docs/EXTENDING.md](docs/EXTENDING.md).

## Extending it

Add a listener, feed, or sink by subclassing the base and registering an entry
point — the [full walkthrough is in docs/EXTENDING.md](docs/EXTENDING.md):

```python
# my_pkg/gelf_sink.py
from robotframework_superset import BaseSink, Event

class GelfSink(BaseSink):
    def emit(self, event: Event) -> None:
        ...  # ship event.to_dict() to Graylog
```

```toml
# my_pkg/pyproject.toml
[project.entry-points."robotframework_superset.sinks"]
graylog = "my_pkg.gelf_sink:GelfSink"
```

```python
from robotframework_superset.registry import load_sink
sink = load_sink("graylog", host="graylog.local", port=12201)
```

The design deliberately aligns with
[rf-graylog](https://github.com/tkarcheski/rf-graylog)'s listener/transport
split, so a GELF transport can become just another sink.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — core abstractions and contracts.
- [docs/TIMESTAMPS.md](docs/TIMESTAMPS.md) — the dual-clock model, with worked
  examples for durations and cross-source joins.
- [docs/EXTENDING.md](docs/EXTENDING.md) — write a custom listener, feed, or
  sink; a complete example plugin.
- [docs/COMPONENTS.md](docs/COMPONENTS.md) — per-component configuration and
  arguments.
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev setup and the CI gates.

## License

[Apache-2.0](LICENSE).
