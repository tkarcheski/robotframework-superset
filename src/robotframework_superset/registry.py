"""Plugin registry and discovery.

Listeners, feeds, and sinks are discovered through Python entry points so
third parties can ship their own without modifying this package. Built-in
implementations are registered in ``pyproject.toml`` under the same groups,
making them indistinguishable from external plugins.

Entry-point groups:
    ``robotframework_superset.listeners``
    ``robotframework_superset.feeds``
    ``robotframework_superset.sinks``

Example (external plugin's ``pyproject.toml``)::

    [project.entry-points."robotframework_superset.sinks"]
    graylog = "my_pkg.gelf_sink:GelfSink"

Then::

    from robotframework_superset.registry import load_sink
    sink = load_sink("graylog", host="graylog.local", port=12201)
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any, Dict, List

_LISTENERS = "robotframework_superset.listeners"
_FEEDS = "robotframework_superset.feeds"
_SINKS = "robotframework_superset.sinks"


def _load(group: str, name: str) -> Any:
    """Return the class/factory registered as ``name`` in ``group``.

    Raises:
        KeyError: if no plugin with that name is registered in the group.
    """
    for ep in entry_points(group=group):
        if ep.name == name:
            return ep.load()
    available = ", ".join(sorted(list_plugins(group))) or "<none>"
    raise KeyError(f"No plugin '{name}' in group '{group}'. Available: {available}")


def list_plugins(group: str) -> List[str]:
    """Return the names of every plugin registered under ``group``."""
    return [ep.name for ep in entry_points(group=group)]


def available() -> Dict[str, List[str]]:
    """Return a mapping of each group to its registered plugin names."""
    return {
        "listeners": list_plugins(_LISTENERS),
        "feeds": list_plugins(_FEEDS),
        "sinks": list_plugins(_SINKS),
    }


def load_listener(name: str, *args: Any, **kwargs: Any) -> Any:
    """Instantiate the listener registered as ``name``."""
    return _load(_LISTENERS, name)(*args, **kwargs)


def load_feed(name: str, *args: Any, **kwargs: Any) -> Any:
    """Instantiate the feed registered as ``name``."""
    return _load(_FEEDS, name)(*args, **kwargs)


def load_sink(name: str, *args: Any, **kwargs: Any) -> Any:
    """Instantiate the sink registered as ``name``."""
    return _load(_SINKS, name)(*args, **kwargs)
