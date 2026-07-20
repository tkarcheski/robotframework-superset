# Extending robotframework-superset

Plugins are ordinary Python packages registered through entry points. They do
not import private internals or modify this repository. The three supported
groups are:

- `robotframework_superset.listeners`
- `robotframework_superset.feeds`
- `robotframework_superset.sinks`

## Complete sink plugin

This minimal package persists one JSON object per line while honoring the sink
contract. Its layout is:

```text
rfs-jsonl/
├── pyproject.toml
└── src/rfs_jsonl/__init__.py
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "rfs-jsonl"
version = "0.1.0"
dependencies = ["robotframework-superset>=0.1"]

[project.entry-points."robotframework_superset.sinks"]
jsonl = "rfs_jsonl:JsonLinesSink"

[tool.hatch.build.targets.wheel]
packages = ["src/rfs_jsonl"]
```

`src/rfs_jsonl/__init__.py`:

```python
from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

from robotframework_superset import BaseSink, Event


class JsonLinesSink(BaseSink):
    def __init__(self, path: str = "events.jsonl") -> None:
        self._stream: TextIO = Path(path).open("a", encoding="utf-8")

    def emit(self, event: Event) -> None:
        try:
            self._stream.write(json.dumps(event.to_dict()) + "\n")
        except OSError as exc:
            print(f"[rfs-jsonl] write failed ({exc}); event dropped")

    def flush(self) -> None:
        try:
            self._stream.flush()
        except OSError as exc:
            print(f"[rfs-jsonl] flush failed ({exc})")

    def close(self) -> None:
        self.flush()
        self._stream.close()
```

Install and load it without changing `robotframework-superset`:

```bash
python -m pip install -e ./rfs-jsonl
python -c "from robotframework_superset.registry import available; print(available())"
```

```python
from robotframework_superset.registry import load_sink

sink = load_sink("jsonl", path="run-events.jsonl")
```

Listener plugins receive Robot Framework arguments as colon-delimited
positional strings. Built-ins use the shared `key=value` convention:

```bash
robot --listener package.Listener:sink=jsonl:keyword_events=false tests/
```

Use `parse_listener_arguments()` from
`robotframework_superset.listeners.base` to adopt the same parsing behavior.
Unknown plugin names raise `KeyError` and list the available names, making
installation mistakes visible immediately.
