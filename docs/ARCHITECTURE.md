# Architecture

`robotframework-superset` turns everything that happens during a test run into
a single, uniformly-timestamped stream of **events**, persists them to a
**sink** (a Superset-backed database by default), and lets Apache Superset
visualize them. This document defines the core abstractions and the contract
every extension must honor.

## 1. The event model

An **event** is one observation from a producer. It is the only currency in the
system — listeners and feeds produce events, sinks consume them.

```python
@dataclass
class Event:
    event_type: str          # "robot.test.end", "console.rx", "openai.response"
    source: str              # producer id: "robot", "console:dut0", "ollama"
    wall_clock: datetime     # UTC, tz-aware, microsecond precision
    monotonic_ns: int        # time.monotonic_ns() at capture
    level: EventLevel        # TRACE|DEBUG|INFO|WARN|ERROR
    message: str             # one-line human summary
    duration_ns: int         # measured duration, or -1 if N/A
    payload: dict            # JSON-serializable structured detail
```

### 1.1 Precise timestamps — the central invariant

Every event carries **two** timestamps, both captured at the *ingest boundary*
(the moment the producer observes the thing, before any parsing):

| Field          | Clock                | Use it for                                   | Never use it for                     |
|----------------|----------------------|----------------------------------------------|--------------------------------------|
| `wall_clock`   | `datetime.now(utc)`  | absolute time, cross-host ordering, display  | durations (can jump backward)        |
| `monotonic_ns` | `time.monotonic_ns()`| durations, intra-process ordering            | absolute time, cross-process compare |

**Why both?** Wall-clock time can step backward (NTP correction, DST, manual
set), silently corrupting `end - start` duration math. The monotonic clock
never moves backward, but has no absolute meaning and is only comparable within
one process. Recording both, per event, means a consumer can always pick the
right clock. A sink **MUST** persist both.

**Precision requirements:**
- `wall_clock` is stored with at least microsecond precision and an explicit
  timezone (UTC). Serialized as ISO-8601, e.g. `2026-07-17T12:34:56.123456+00:00`.
- `monotonic_ns` is integer nanoseconds.
- `duration_ns` is computed from two `monotonic_ns` reads (start/end) — not
  from wall-clock subtraction.

### 1.2 Event types

Dotted, source-scoped, lower-case. Producers own their namespace:

- `robot.run.start` / `robot.run.end`
- `robot.suite.start` / `robot.suite.end`
- `robot.test.start` / `robot.test.end`
- `robot.keyword.start` / `robot.keyword.end`
- `robot.log`
- `console.open` / `console.rx` / `console.tx` / `console.close`
- `openai.request` / `openai.response` / `openai.error`
- `ollama.request` / `ollama.response` / `ollama.error`

## 2. Producers: listeners and feeds

Two producer kinds share one job — emit events — but differ in how they are
driven.

### 2.1 Listener (`BaseListener`)

A Robot Framework **Listener API v3** implementation. RF *pushes* lifecycle
callbacks; the base class handles suite-depth tracking (to find the outermost
"run" boundary) and gives subclasses `on_*` template hooks plus a single
`_emit(...)` helper that stamps both clocks and routes to the sink,
skip-and-logging on sink failure so telemetry never fails a test.

Concrete listeners:
- **`RobotFrameworkListener`** — the standard listener; maps the full RF
  lifecycle to events.
- **`ConsoleListener`** — taps a device console over telnet (client mode) or
  accepts a session (server mode); mirrors every line as `console.rx`/`.tx`.

### 2.2 Feed (`BaseFeed`)

Wraps activity that RF does not push — an HTTP call, a socket read. The
canonical pattern measures *around* a call with a context manager so
`duration_ns` is accurate:

```python
with feed.record("openai.response", model="gpt-4o") as rec:
    resp = client.chat(...)
    rec["tokens"] = resp.usage.total_tokens
# Event emitted here: duration_ns = monotonic delta across the block.
```

Concrete feeds:
- **`OpenAIFeed`** — OpenAI / Azure / OpenAI-compatible chat completions.
- **`OllamaFeed`** — Ollama generate/chat; also captures Ollama's own
  server-side nanosecond timings alongside the client-measured duration.

## 3. Sinks

A **sink** is where events rest. The protocol is deliberately narrow:

```python
class Sink(Protocol):
    def emit(self, event: Event) -> None: ...
    def emit_many(self, events: Iterable[Event]) -> None: ...
    def flush(self) -> None: ...
    def close(self) -> None: ...
```

Rules:
- `emit` MUST NOT raise on transient backend failure — skip-and-log instead, so
  one bad event never aborts a run. Exceptions are reserved for programmer error.
- A sink MUST persist both timestamps.
- Buffering/batching is the sink's concern; `flush` forces a write, `close`
  releases resources.

Reference sinks:
- **`DatabaseSink`** — SQLAlchemy → PostgreSQL (prod) / SQLite (local, tests).
  Superset reads the same database. Writes are buffered, auto-flushed at the
  configured batch size, and forced by `flush()`/`close()`. SQLite stores the
  wall clock as ISO text so timezone information survives round trips. Base table:

  ```sql
  CREATE TABLE events (
      id           BIGSERIAL PRIMARY KEY,
      event_type   TEXT        NOT NULL,
      source       TEXT        NOT NULL,
      wall_clock   TIMESTAMPTZ NOT NULL,
      monotonic_ns BIGINT      NOT NULL,
      level        TEXT        NOT NULL,
      message      TEXT        NOT NULL DEFAULT '',
      duration_ns  BIGINT      NOT NULL DEFAULT -1,
      payload      JSONB       NOT NULL DEFAULT '{}'
  );
  ```

- **`GelfSink`** — ships each event to a Graylog **GELF-over-TCP** input, so
  GELF is just another sink (issue #14). The GELF-over-TCP transport is vendored
  from rf-graylog (see §5), keeping the sink stdlib-only. Both timestamps are
  preserved: `wall_clock` becomes the GELF `timestamp` (Unix epoch, microsecond
  precision) *and* `_wall_clock` (the original ISO-8601 string); `monotonic_ns`
  becomes `_monotonic_ns`. `level` maps to the numeric syslog/GELF severity;
  `payload` keys are flattened into `_`-prefixed additional fields.
- **`MultiSink`** — a composite that fans one event out to several sinks, so a
  single run can persist to Superset **and** forward to Graylog at once:
  `MultiSink(DatabaseSink(...), GelfSink(...))`. A failing child sink is isolated
  (skip-and-log) so the others still receive the event.
- **`NullSink`** — discards events (telemetry off).
- **`MemorySink`** — keeps events in a list (tests).

### 3.1 Choosing a sink: Superset, GELF, or both

| Goal                                                        | Sink                              |
|-------------------------------------------------------------|-----------------------------------|
| Structured, queryable history for dashboards and SQL        | `DatabaseSink` (Superset-backed)  |
| Live log search, alerting, correlation with app/system logs | `GelfSink` (Graylog)              |
| Both at once — dashboards *and* live search                 | `MultiSink(DatabaseSink, GelfSink)` |
| Telemetry off (default)                                     | `NullSink`                        |

- Use the **Superset sink** as the system of record: it stores every column
  (both clocks, `duration_ns`, `payload` as JSONB) for retrospective analysis,
  charts, and durable history.
- Use the **GELF sink** for real-time operations: Graylog streams, saved
  searches, and threshold alerts, and to correlate test events with the same
  Graylog instance already collecting application and infrastructure logs.
- Use **both** (via `MultiSink`) when a run must be both dashboarded and
  watched live. Sinks are independent, so a Graylog outage never blocks the
  database write, and vice versa.

## 4. Plugin registration

Listeners, feeds, and sinks are discovered through Python **entry points**, so
third parties extend the framework without modifying it. Built-ins register
under the same groups and are indistinguishable from external plugins:

- `robotframework_superset.listeners`
- `robotframework_superset.feeds`
- `robotframework_superset.sinks`

```python
from robotframework_superset.registry import load_sink, available
available()                    # {"listeners": [...], "feeds": [...], "sinks": [...]}
sink = load_sink("db", database_url="postgresql://...")
```

## 5. Relationship to rf-graylog

[rf-graylog](https://github.com/tkarcheski/rf-graylog) already splits a shared
transport from multiple listener implementations (builtin, telnet, LLM) and
uses a process-wide registry so a library and its listener share one transport.
This project generalizes that shape: its **transport** becomes our **sink**,
its **listeners** map onto our listener/feed split, and GELF is packaged as
just another sink plugin (`GelfSink`, §3). Aligning the abstractions means
rf-graylog and robotframework-superset share concepts and, eventually, code.

**Packaging decision (issue #14).** The GELF sink lives **in this repo**,
registered under the `robotframework_superset.sinks` group as `graylog`, rather
than as a separate package inside rf-graylog that depends on this one. This is
the smaller, reversible option: it keeps the reference sink discoverable out of
the box and adds no cross-repo dependency edge. The GELF-over-TCP transport is
**vendored** (a minimal, stdlib-only adaptation of rf-graylog's
`robot_graylog_common.transport`) rather than imported, so the sink has no
runtime dependency on rf-graylog and registers unconditionally — importing the
plugin can never fail for a missing optional dependency. An empty `graylog`
extra is published as a forward-compatible hook for consumers who would rather
install rf-graylog and swap the transport. rf-graylog's public API is
untouched, per the issue's no-breaking-change constraint.

## 6. Deployment

`infra/docker-compose.yml` brings up PostgreSQL, Redis, and Superset.
`infra/superset/` holds the image, configuration, and an idempotent bootstrap
for the `events` dataset, derived latency/error/LLM views, charts, and the
**RF + LLM Observability** dashboard. Operational diagnostics and event-data
sanitization live under `infra/scripts/`.
