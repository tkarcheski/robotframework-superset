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
    assert "graylog" in sinks
    assert "multi" in sinks


def test_load_graylog_sink_or_skip() -> None:
    from robotframework_superset.sinks.gelf import GelfSink

    sinks = registry.list_plugins("robotframework_superset.sinks")
    if "graylog" not in sinks:
        pytest.skip("package not installed; entry points unavailable")
    sink = registry.load_sink("graylog", host="127.0.0.1", port=12201)
    assert isinstance(sink, GelfSink)


def test_load_unknown_raises() -> None:
    with pytest.raises(KeyError):
        registry.load_sink("does-not-exist")
