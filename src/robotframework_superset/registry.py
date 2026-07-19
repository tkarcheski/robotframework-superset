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

from importlib import import_module
from importlib.metadata import entry_points
from typing import Any, Dict, List, Tuple, Union

_LISTENERS = "robotframework_superset.listeners"
_FEEDS = "robotframework_superset.feeds"
_SINKS = "robotframework_superset.sinks"

# Fallback map so the built-in sinks resolve by name even when the package's
# distribution metadata (and thus its entry points) is unavailable — e.g. a
# vendored source tree or a submodule checkout that was never pip-installed.
# Entry points take precedence so an external plugin can shadow a name.
_BUILTIN_SINKS: Dict[str, str] = {
    "null": "robotframework_superset.sinks.null:NullSink",
    "memory": "robotframework_superset.sinks.null:MemorySink",
    "stdout": "robotframework_superset.sinks.null:StdoutSink",
    "db": "robotframework_superset.sinks.db:DatabaseSink",
}


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


def resolve_sink(name: str, **kwargs: Any) -> Any:
    """Instantiate a sink by name: entry points first, then the builtin map.

    This is what listener/feed ``sink=<name>`` arguments go through, so
    ``--listener ...:sink=db`` works both for pip-installed deployments
    (entry points) and vendored source trees (builtin fallback).

    Raises:
        KeyError: unknown name; the message lists every resolvable name.
    """
    for ep in entry_points(group=_SINKS):
        if ep.name == name:
            return ep.load()(**kwargs)
    if name in _BUILTIN_SINKS:
        module_path, _, attr = _BUILTIN_SINKS[name].partition(":")
        return getattr(import_module(module_path), attr)(**kwargs)
    available_names = sorted(set(list_plugins(_SINKS)) | set(_BUILTIN_SINKS))
    raise KeyError(f"No sink '{name}'. Available: {', '.join(available_names)}")


def parse_kwargs(args: Tuple[str, ...]) -> Dict[str, Union[str, int, float, bool]]:
    """Parse Robot Framework listener arguments of the form ``key=value``.

    RF passes everything after each ``:`` in ``--listener Mod:sink=db:batch_size=50``
    as positional strings; this turns them into a kwargs dict. Only the first
    ``=`` splits key from value, so URLs and DSNs survive intact. Values are
    coerced: ``true``/``false`` (case-insensitive) → bool, integer literals →
    int, float literals → float, everything else stays a string.

    Raises:
        ValueError: an argument has no ``=`` at all.
    """
    parsed: Dict[str, Union[str, int, float, bool]] = {}
    for arg in args:
        key, sep, raw = arg.partition("=")
        if not sep:
            raise ValueError(f"Listener argument {arg!r} is not of the form key=value")
        parsed[key] = _coerce(raw)
    return parsed


def _coerce(raw: str) -> Union[str, int, float, bool]:
    """Coerce a listener-arg string to bool/int/float where unambiguous."""
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw
