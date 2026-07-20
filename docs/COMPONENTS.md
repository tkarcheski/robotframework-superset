# Component reference

Configuration and usage for each built-in component. Components marked
**scaffold** expose their final interface (constructor arguments, entry-point
name, event types) but raise `NotImplementedError` until their tracking issue
lands; the arguments documented here are the committed contract. Components
marked **ready** work today.

Environment-variable names come from [`.env.example`](../.env.example). Values
are never printed to events — record names, not secrets.

## Listeners

Registered under `robotframework_superset.listeners`. Attach on the Robot
command line with `--listener <import-path>[:arg=value...]`.

### `RobotFrameworkListener` — scaffold (#4)

The standard listener; maps the full RF lifecycle to events.

- Entry point: `robot`
- Import path:
  `robotframework_superset.listeners.robot_listener.RobotFrameworkListener`
- Constructor: `RobotFrameworkListener(sink=None, source="robot")`
- Event types: `robot.run.start` / `.end`, `robot.suite.start` / `.end`,
  `robot.test.start` / `.end` (with `status` + `duration_ns`),
  `robot.keyword.start` / `.end` (with `duration_ns`), `robot.log`.

```bash
robot --listener robotframework_superset.listeners.robot_listener.RobotFrameworkListener tests/
```

### `ConsoleListener` — scaffold (#5)

Taps a device console and mirrors every line as an event.

- Entry point: `console`
- Import path: `robotframework_superset.listeners.console.ConsoleListener`
- Constructor:
  `ConsoleListener(sink=None, mode="client", host="127.0.0.1", port=2323, channel="console")`
- `source` is recorded as `console:<channel>`.
- Event types: `console.open`, `console.rx`, `console.tx`, `console.close`.

| Arg       | Default       | Meaning                                                        |
|-----------|---------------|----------------------------------------------------------------|
| `mode`    | `client`      | `client` taps a remote console server (the common lab/CI case; no inbound firewall change). `server` runs a small telnet server and accepts a session. |
| `host`    | `127.0.0.1`   | Console-server host (client) or bind address (server). Env: `CONSOLE_HOST`. |
| `port`    | `2323`        | Telnet TCP port. Env: `CONSOLE_PORT`.                          |
| `channel` | `console`     | Human label for this console, used in `source`.                |

```bash
robot --listener robotframework_superset.listeners.console.ConsoleListener:mode=client:host=10.0.0.5:port=2001 tests/
```

Each received line is stamped with `monotonic_ns` **before** decoding, to
minimize jitter between a byte arriving and its timestamp.

### `BaseListener` — ready

The base every listener extends. Subclass it directly for a custom listener
(see [EXTENDING §4](EXTENDING.md#4-writing-a-listener)). Provides suite-depth
tracking, the `_emit` ingest boundary, and `on_*` template hooks.

## Feeds

Registered under `robotframework_superset.feeds`. Instantiated in code (not on
the RF command line) and given a sink.

### `OpenAIFeed` — scaffold (#6)

Instruments OpenAI / Azure / OpenAI-compatible chat completions.

- Entry point: `openai` (install extra: `pip install robotframework-superset[openai]`)
- Constructor: `OpenAIFeed(sink=None, base_url="", model="")`
- Event types: `openai.request`, `openai.response` (usage, `finish_reason`,
  `duration_ns`, model id), `openai.error`.

| Arg        | Default                             | Env               |
|------------|-------------------------------------|-------------------|
| `base_url` | `https://api.openai.com/v1`         | `OPENAI_BASE_URL` |
| `model`    | —                                   | —                 |

`OPENAI_API_KEY` is read from the environment and **never** placed in an event
payload. If it is absent the feed skips-and-logs rather than failing.

### `OllamaFeed` — scaffold (#7)

Instruments a local/LAN Ollama server (`/api/generate`, `/api/chat`).

- Entry point: `ollama` (install extra: `pip install robotframework-superset[ollama]`)
- Constructor: `OllamaFeed(sink=None, endpoint="", model="")`
- Event types: `ollama.request`, `ollama.response`, `ollama.error`.

| Arg        | Default                    | Env              |
|------------|----------------------------|------------------|
| `endpoint` | `http://localhost:11434`   | `OLLAMA_ENDPOINT`|
| `model`    | —                          | `DEFAULT_MODEL`  |

The response event carries Ollama's server-side nanosecond timings
(`total_duration`, `load_duration`, `eval_count`, `eval_duration`) in `payload`
alongside the client-measured `duration_ns`; see
[TIMESTAMPS §4](TIMESTAMPS.md#4-server-reported-vs-client-measured-durations).
An Ollama server is **unauthenticated** — never bind `OLLAMA_ENDPOINT` to a
public interface.

### `BaseFeed` — ready

The base every feed extends, including `record()`. Usable directly for
ad-hoc instrumentation (see [EXTENDING §3](EXTENDING.md#3-writing-a-feed)).

## Sinks

Registered under `robotframework_superset.sinks`. Loaded by name via
`registry.load_sink(name, **kwargs)`.

### `DatabaseSink` — scaffold (#8)

Writes events to the Superset-backed SQL database (PostgreSQL in production,
SQLite locally).

- Entry point: `db` (install extra: `pip install robotframework-superset[db]`)
- Constructor: `DatabaseSink(database_url="", batch_size=50)`
- `database_url` defaults to the `DATABASE_URL` env var (a SQLAlchemy URL).
- Writes are buffered and flushed in batches of `batch_size`;
  `flush`/`close` force a final write.
- Target schema (finalized in #8):

  ```sql
  CREATE TABLE events (
      id            BIGSERIAL PRIMARY KEY,
      event_type    TEXT        NOT NULL,
      source        TEXT        NOT NULL,
      wall_clock    TIMESTAMPTZ NOT NULL,
      monotonic_ns  BIGINT      NOT NULL,
      level         TEXT        NOT NULL,
      message       TEXT        NOT NULL DEFAULT '',
      duration_ns   BIGINT      NOT NULL DEFAULT -1,
      payload       JSONB       NOT NULL DEFAULT '{}'
  );
  ```

### `NullSink` — ready

Discards every event. The safe default when telemetry is disabled.

- Entry point: `null`
- Constructor: `NullSink()`

### `MemorySink` — ready

Keeps events in a list (`.events`) for tests and assertions. Not registered as
an entry point; import from `robotframework_superset.sinks.null`.

## Superset dashboards — scaffold (#9)

[`infra/docker-compose.yml`](../infra/docker-compose.yml) brings up PostgreSQL,
Redis, and Apache Superset; [`infra/superset/`](../infra/superset/) holds the
image and `superset_config.py`. Superset reads the same PostgreSQL database the
DB sink writes, charting the `events` table. Automated dashboard bootstrapping
(datasets, charts, dashboards) is tracked in #9. See the
[Quickstart](../README.md#quickstart) for bringing the stack up.
