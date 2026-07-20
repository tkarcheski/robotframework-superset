# Example plugin: JSONL sink

A complete, dependency-free [sink](../../docs/ARCHITECTURE.md#3-sinks) that
appends every event to a newline-delimited JSON file. It is the worked example
for [docs/EXTENDING.md](../../docs/EXTENDING.md).

## Files

- `rfs_jsonl_sink.py` — the `JsonlSink` implementation.
- `pyproject.toml` — packaging that registers the sink under the
  `robotframework_superset.sinks` entry-point group.

## Run it directly

No install required — the module runs as a script and writes `events.jsonl`:

```bash
python rfs_jsonl_sink.py
```

## Use it programmatically

```python
from rfs_jsonl_sink import JsonlSink
from robotframework_superset import Event

sink = JsonlSink(path="events.jsonl")
sink.emit(Event(event_type="demo.start", source="example", message="hello"))
sink.close()
```

## Install it as a discoverable plugin

```bash
pip install .
```

Once installed, the sink is loadable by name through the registry, exactly like
a built-in:

```python
from robotframework_superset.registry import load_sink

sink = load_sink("jsonl", path="events.jsonl")
```

The automated test at
[`tests/test_examples.py`](../../tests/test_examples.py) loads this module and
exercises it end-to-end, so CI proves the example imports and runs.
