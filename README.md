# robotframework-superset

[![CI](https://github.com/tkarcheski/robotframework-superset/actions/workflows/ci.yml/badge.svg)](https://github.com/tkarcheski/robotframework-superset/actions/workflows/ci.yml)

Extensible **listeners** and precisely-timestamped **event feeds** for
[Robot Framework](https://robotframework.org/), visualized with
[Apache Superset](https://superset.apache.org/).

Capture what happens during a test run — Robot lifecycle events, raw
console/telnet traffic, and LLM API calls (OpenAI, Ollama) — as a single
stream of events, each stamped with **two clocks** (wall-clock and monotonic),
persist them to a Superset-backed database, and explore them in dashboards.

> Status: **v0.1 implementation in progress.** The event contract, plugin
> registry, standard Robot listener, SQL sink, and starter Superset deployment
> are implemented. Console and LLM producers remain tracked under the migration epic. See
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

```bash
# From this checkout (use pip install robotframework-superset[db] after release):
python -m pip install -e ".[db]"

# Bring up PostgreSQL + Superset locally:
cp .env.example .env
# Replace the placeholder database, Superset, and admin credentials in .env.
make up

# Export DATABASE_URL for the listener and attach the database sink:
set -a; source .env; set +a
robot --listener robotframework_superset.listeners.robot_listener.RobotFrameworkListener:sink=db path/to/tests/
```

Open <http://localhost:8088> and select **RF + LLM Observability**. The
bootstrap is idempotent; use `make bootstrap` to refresh its database,
datasets, charts, and dashboard. `make diagnose` checks the complete
environment → database → schema → data → Superset path.

Keyword events are enabled by default and can be high-volume. Disable them
with `:keyword_events=false` on the listener argument. Sinks are discovered
through the `robotframework_superset.sinks` entry-point group; `sink=db` uses
`DATABASE_URL`, while `sink=null` disables persistence.

## GELF / Graylog sink

GELF is a first-class, built-in sink: `GelfSink` ships every event to a Graylog
GELF-over-TCP input. Both clocks are preserved on the wire — `wall_clock`
becomes the GELF `timestamp` (Unix epoch, microsecond precision) *and*
`_wall_clock` (the original ISO-8601 string); `monotonic_ns` becomes
`_monotonic_ns`.

```python
from robotframework_superset.registry import load_sink
sink = load_sink("graylog", host="graylog.local", port=12201)
```

The GELF-over-TCP transport is vendored from
[rf-graylog](https://github.com/tkarcheski/rf-graylog) (stdlib only), so the
sink needs no extra dependencies and registers out of the box. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) §5 for the packaging rationale.

### Superset sink vs GELF sink vs both

| Goal                                                    | Sink                                 |
|---------------------------------------------------------|--------------------------------------|
| Queryable history for dashboards and SQL                | `DatabaseSink` (Superset-backed)     |
| Live log search, alerting, correlation with other logs  | `GelfSink` (Graylog)                 |
| Both at once                                            | `MultiSink(DatabaseSink, GelfSink)`  |

`MultiSink` fans one event out to several sinks, so a single run can be
dashboarded in Superset and watched live in Graylog without either backend
blocking the other:

```python
from robotframework_superset.sinks.db import DatabaseSink
from robotframework_superset.sinks.gelf import GelfSink
from robotframework_superset.sinks.multi import MultiSink

sink = MultiSink(
    DatabaseSink(database_url="postgresql://..."),
    GelfSink(host="graylog.local", port=12201),
)
```

## Extending it

See the complete [extension guide](docs/EXTENDING.md) for a runnable external
package, installation, discovery, and listener argument conventions.

Write your own listener, feed, or sink by subclassing the base and registering
an entry point:

```python
# my_pkg/stdout_sink.py
from robotframework_superset import BaseSink, Event

class StdoutSink(BaseSink):
    def emit(self, event: Event) -> None:
        print(event.to_dict())  # ship event.to_dict() wherever it needs to go
```

```toml
# my_pkg/pyproject.toml
[project.entry-points."robotframework_superset.sinks"]
stdout = "my_pkg.stdout_sink:StdoutSink"
```

```python
from robotframework_superset.registry import load_sink
sink = load_sink("stdout")
```

The design deliberately aligns with
[rf-graylog](https://github.com/tkarcheski/rf-graylog)'s listener/transport
split, so a GELF transport becomes just another sink — as `GelfSink` already
demonstrates.

## Releasing

Publishing to PyPI is a single, owner-gated action authenticated with Trusted
Publishing (OIDC) — no API token is stored in repository secrets. See
[docs/RELEASING.md](docs/RELEASING.md) for the one-time setup and the
tag-to-publish flow.

## License

[Apache-2.0](LICENSE).
