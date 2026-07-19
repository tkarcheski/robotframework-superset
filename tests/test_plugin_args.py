"""Tests for listener-argument parsing and sink resolution."""

from __future__ import annotations

import pytest

from robotframework_superset import registry
from robotframework_superset.sinks.null import MemorySink, NullSink, StdoutSink


def test_parse_kwargs_splits_and_coerces() -> None:
    parsed = registry.parse_kwargs(("sink=db", "batch_size=50", "verbose=true", "host=example"))
    assert parsed == {"sink": "db", "batch_size": 50, "verbose": True, "host": "example"}


def test_parse_kwargs_coercion_rules() -> None:
    parsed = registry.parse_kwargs(("a=1.5", "b=false", "c=0", "d=", "e=None"))
    assert parsed == {"a": 1.5, "b": False, "c": 0, "d": "", "e": "None"}


def test_parse_kwargs_value_may_contain_equals() -> None:
    # Only the first '=' splits key from value (URLs, DSNs).
    parsed = registry.parse_kwargs(("url=postgresql://u:p@h/db?sslmode=require",))
    assert parsed == {"url": "postgresql://u:p@h/db?sslmode=require"}


def test_parse_kwargs_rejects_malformed() -> None:
    with pytest.raises(ValueError, match="key=value"):
        registry.parse_kwargs(("no-equals-sign",))


def test_resolve_sink_builtin_names_work_without_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    # Simulate a bare source tree where distribution metadata is absent.
    monkeypatch.setattr(registry, "entry_points", lambda group: [])
    assert isinstance(registry.resolve_sink("null"), NullSink)
    assert isinstance(registry.resolve_sink("memory"), MemorySink)
    assert isinstance(registry.resolve_sink("stdout"), StdoutSink)


def test_resolve_sink_prefers_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    from importlib.metadata import EntryPoint

    ep = EntryPoint(
        name="custom",
        value="robotframework_superset.sinks.null:MemorySink",
        group="robotframework_superset.sinks",
    )
    monkeypatch.setattr(registry, "entry_points", lambda group: [ep])
    assert isinstance(registry.resolve_sink("custom"), MemorySink)


def test_resolve_sink_unknown_raises_with_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "entry_points", lambda group: [])
    with pytest.raises(KeyError, match="memory"):
        registry.resolve_sink("nope")


def test_resolve_sink_passes_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "entry_points", lambda group: [])
    sink = registry.resolve_sink("db", database_url="sqlite://", batch_size=7)
    assert sink.batch_size == 7
