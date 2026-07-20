# robotframework-superset

[![CI](https://github.com/tkarcheski/robotframework-superset/actions/workflows/ci.yml/badge.svg)](https://github.com/tkarcheski/robotframework-superset/actions/workflows/ci.yml)

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

## Quickstart (placeholder)

```bash
pip install robotframework-superset            # once published to PyPI

# Bring up PostgreSQL + Superset locally:
cp .env.example .env                           # edit credentials
docker compose -f infra/docker-compose.yml --env-file .env up -d

# Attach the standard listener to a Robot run (once implemented):
robot --listener robotframework_superset.listeners.robot_listener.RobotFrameworkListener tests/
```

Full end-to-end usage lands with the concrete implementations — see the epic.

## Extending it

Write your own listener, feed, or sink by subclassing the base and registering
an entry point:

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

## Releasing

Publishing to PyPI is a single, owner-gated action authenticated with Trusted
Publishing (OIDC) — no API token is stored in repository secrets. See
[docs/RELEASING.md](docs/RELEASING.md) for the one-time setup and the
tag-to-publish flow.

## License

[Apache-2.0](LICENSE).
