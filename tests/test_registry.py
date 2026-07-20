"""Tests for entry-point plugin discovery.

These assert the built-in plugins are registered once the package is installed
(``pip install -e .``). They are skipped gracefully if the distribution
metadata is unavailable (e.g. running from a bare source tree).
"""

from __future__ import annotations

import pytest

from robotframework_superset import registry


def test_available_groups_present() -> None:
    groups = registry.available()
    assert set(groups) == {"listeners", "feeds", "sinks"}


def test_builtin_sinks_registered_or_skip() -> None:
    sinks = registry.list_plugins("robotframework_superset.sinks")
    if not sinks:
        pytest.skip("package not installed; entry points unavailable")
    assert "null" in sinks
    assert "db" in sinks


def test_builtin_plugins_load_by_name_or_skip() -> None:
    plugins = registry.available()
    if not plugins["sinks"]:
        pytest.skip("package not installed; entry points unavailable")
    sink = registry.load_sink("null")
    listener = registry.load_listener("robot", "sink=null")
    assert sink.__class__.__name__ == "NullSink"
    assert listener.sink.__class__.__name__ == "NullSink"


def test_load_unknown_raises() -> None:
    with pytest.raises(KeyError, match="Available"):
        registry.load_sink("does-not-exist")


def test_external_entry_point_is_discoverable(monkeypatch: pytest.MonkeyPatch) -> None:
    class ExampleSink:
        def __init__(self, value: int = 0) -> None:
            self.value = value

    class EntryPoint:
        name = "example"

        def load(self) -> type[ExampleSink]:
            return ExampleSink

    def fake_entry_points(*, group: str) -> list[EntryPoint]:
        return [EntryPoint()] if group == "robotframework_superset.sinks" else []

    monkeypatch.setattr(registry, "entry_points", fake_entry_points)
    assert registry.list_plugins("robotframework_superset.sinks") == ["example"]
    assert registry.load_sink("example", value=7).value == 7
