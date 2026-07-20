"""Verify the shipped example plugin imports and runs.

Acceptance criterion for issue #11: the extension guide's example plugin is
real code that imports and runs. This loads the JSONL sink from ``examples/``
and exercises it end-to-end so CI proves the guide's example works.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from robotframework_superset.event import Event
from robotframework_superset.sink import Sink

_EXAMPLE = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "jsonl_sink_plugin"
    / "rfs_jsonl_sink.py"
)


def _load_jsonl_sink() -> Any:
    spec = importlib.util.spec_from_file_location("rfs_jsonl_sink", _EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.JsonlSink


def test_example_plugin_file_exists() -> None:
    assert _EXAMPLE.is_file()


def test_jsonl_sink_satisfies_protocol() -> None:
    jsonl_sink = _load_jsonl_sink()
    assert isinstance(jsonl_sink(path="unused"), Sink)


def test_jsonl_sink_persists_both_clocks(tmp_path: Path) -> None:
    jsonl_sink = _load_jsonl_sink()
    out = tmp_path / "events.jsonl"
    sink = jsonl_sink(path=str(out), buffer_size=2)
    sink.emit(Event(event_type="robot.test.end", source="robot", duration_ns=42))
    sink.emit_many(
        Event(event_type="console.rx", source="console:dut0", message=f"line {i}")
        for i in range(3)
    )
    sink.close()

    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 4

    first = json.loads(lines[0])
    # The central invariant: every persisted row carries BOTH clocks.
    assert first["wall_clock"].endswith("+00:00")
    assert isinstance(first["monotonic_ns"], int)
    assert first["duration_ns"] == 42
