# Timestamps: the dual-clock model

Every event in `robotframework-superset` carries **two** timestamps. This is
the framework's central invariant, introduced in
[ARCHITECTURE.md §1.1](ARCHITECTURE.md#11-precise-timestamps--the-central-invariant).
This guide expands that section into a standalone reference with worked
examples: why two clocks exist, which clock answers which question, and how to
join events across processes and hosts without corrupting the math.

## 1. The two clocks

| Field          | Source                | Answers                                       | Wrong for                            |
|----------------|-----------------------|-----------------------------------------------|--------------------------------------|
| `wall_clock`   | `datetime.now(utc)`   | "when, in absolute time?"; cross-host ordering; display | measuring how long something took |
| `monotonic_ns` | `time.monotonic_ns()` | "how long?"; ordering within one process      | absolute time; comparing across processes |

Both are captured at the **ingest boundary** — the instant the producer
observes the thing, before any parsing — so the pair describes the observation,
not the bookkeeping that follows it.

```python
from robotframework_superset import utc_now, monotonic_ns

wall = utc_now()          # 2026-07-17 12:34:56.123456+00:00  (tz-aware UTC)
mono = monotonic_ns()     # 733451920011  (nanoseconds since an arbitrary epoch)
```

`utc_now()` and `monotonic_ns()` are thin, shared capture points so producers
and tests read the clocks the same way.

## 2. Why durations use the monotonic clock

Wall-clock time can step **backward**: an NTP correction, a DST transition, or
a manual clock set can move it. Subtracting two wall-clock reads across such a
step yields a duration that is too small, zero, or negative.

Consider a keyword that genuinely runs for 5 seconds while NTP slews the clock
back by 2 seconds mid-call:

```python
# ANTI-PATTERN — duration from wall-clock subtraction
start = datetime.now(timezone.utc)   # 12:00:05.00
result = do_work()                   # takes 5 s of real time; NTP steps -2 s
end = datetime.now(timezone.utc)     # 12:00:08.00  (not 12:00:10)
duration_s = (end - start).total_seconds()   # 3.0 — WRONG, understated by 2 s
```

The monotonic clock never moves backward, so a duration measured from it is
correct regardless of what the wall clock does:

```python
# CORRECT — duration from paired monotonic reads
start = monotonic_ns()
result = do_work()
duration_ns = monotonic_ns() - start   # 5_000_000_000 — accurate
```

This is why `Event.duration_ns` is defined as the difference of two
`monotonic_ns` reads, never a wall-clock subtraction, and why a sink **MUST**
persist both clocks: the wall clock says *when*, the monotonic clock says *how
long*.

## 3. Getting an accurate duration for free

Feeds provide `record()`, a context manager that reads the monotonic clock at
block entry and exit and emits one event whose `duration_ns` is the delta:

```python
from robotframework_superset import BaseFeed
from robotframework_superset.sinks.null import MemorySink

feed = BaseFeed(sink=MemorySink(), source="ollama")

with feed.record("ollama.response", model="llama3") as rec:
    reply = call_ollama(...)       # whatever the block does is measured
    rec["eval_count"] = reply["eval_count"]
# One event emitted here: duration_ns = monotonic delta across the block,
# both clocks stamped, payload merged with everything added to `rec`.
```

Listeners compute durations the same way — pairing a `monotonic_ns` read taken
at `on_test_start`/`on_keyword_start` with one at the matching `*_end` hook and
passing the difference as `duration_ns` to `_emit`.

## 4. Server-reported vs. client-measured durations

Some producers can report their own timings. The Ollama feed, for example,
captures Ollama's server-side nanosecond fields (`total_duration`,
`load_duration`, `eval_duration`, ...) into the event **payload**, *alongside*
the framework's own client-measured `duration_ns`. Keeping both makes the two
directly comparable: the gap between client `duration_ns` and server
`total_duration` is network + queueing overhead.

```text
duration_ns (client, wall-to-wall around the HTTP call)  = 812_446_101
payload.total_duration (server, Ollama's own clock)      = 640_051_900
                                                            ------------
overhead outside the model                              ≈ 172_394_201 ns
```

Rule of thumb: `duration_ns` is the framework's authoritative measurement;
provider-reported timings are supplementary context and live in `payload`.

## 5. Joining events across sources

Choosing the join clock depends on whether the events came from the **same
process**.

### 5.1 Within one process — order by `monotonic_ns`

A listener and a feed running in the same Robot process share a monotonic
epoch, so `monotonic_ns` gives an exact, step-immune ordering — finer and safer
than wall-clock, which two events microseconds apart might report identically
after rounding.

```sql
-- Interleave a run's events in true capture order (single process).
SELECT wall_clock, event_type, source, message
FROM events
WHERE monotonic_ns BETWEEN :run_start_mono AND :run_end_mono
ORDER BY monotonic_ns;
```

### 5.2 Across processes or hosts — order by `wall_clock`

`monotonic_ns` has **no absolute meaning** and is only comparable within the
process that produced it. Two processes (an RF run on one host, a console tap
on another) have unrelated monotonic epochs, so joining them requires
`wall_clock`:

```sql
-- Line up device console output against the test that was running,
-- across two separate producer processes.
SELECT t.message AS test, c.wall_clock, c.message AS console_line
FROM events t
JOIN events c
  ON c.source LIKE 'console:%'
 AND c.wall_clock BETWEEN t.wall_clock
     AND t.wall_clock + (t.duration_ns * INTERVAL '1 microsecond' / 1000)
WHERE t.event_type = 'robot.test.end'
ORDER BY c.wall_clock;
```

Cross-host wall-clock joins are only as good as clock synchronization between
the hosts. Keep producers NTP-synced; the `wall_clock` field is what makes
those separate streams line up at all.

### 5.3 Decision summary

- Same process, need exact order → `monotonic_ns`.
- Different processes/hosts, need to correlate in time → `wall_clock`.
- Need a duration → always `duration_ns` (derived from `monotonic_ns`).
- Need to display or bucket by calendar time → `wall_clock`.

## 6. Precision and serialization

- `wall_clock` is UTC and tz-aware with at least microsecond precision.
  `Event.to_dict()` renders it via `isoformat()`, e.g.
  `2026-07-17T12:34:56.123456+00:00`. The `+00:00` offset is always present.
- `monotonic_ns` and `duration_ns` are integer nanoseconds. `duration_ns` is
  `-1` when a duration does not apply (a point-in-time event).
- The database sink stores `wall_clock` as `TIMESTAMPTZ` and both nanosecond
  fields as `BIGINT`, preserving full precision for SQL and Superset.

## 7. Common mistakes

- **Computing a duration from `wall_clock`.** Use `duration_ns`. Wall-clock
  subtraction breaks silently on a clock step.
- **Comparing `monotonic_ns` across processes.** The epoch is arbitrary and
  per-process; the numbers are meaningless between producers.
- **Sorting a multi-process report by `monotonic_ns`.** Interleaves unrelated
  epochs into nonsense order; use `wall_clock`.
- **Dropping the timezone.** A naive `wall_clock` cannot be compared across
  hosts and is not the framework's format. Keep it tz-aware UTC.

## See also

- [ARCHITECTURE.md §1](ARCHITECTURE.md#1-the-event-model) — the event model.
- [EXTENDING.md](EXTENDING.md) — emitting correctly-stamped events from a
  custom producer.
- [COMPONENTS.md](COMPONENTS.md) — per-component configuration.
