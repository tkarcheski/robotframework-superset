"""robotframework-superset — extensible listeners and precisely-timestamped
event feeds for Robot Framework, visualized with Apache Superset.

Public surface:
    Event, EventLevel, utc_now, monotonic_ns   -- the core event model
    Sink, BaseSink                             -- the sink protocol
    BaseListener                               -- RF Listener API v3 base
    BaseFeed                                   -- non-RF stream base
    registry                                   -- plugin discovery/loading
"""

from __future__ import annotations

from .event import Event, EventLevel, elapsed_ns, monotonic_ns, utc_now
from .feeds.base import BaseFeed
from .listeners.base import BaseListener
from .sink import BaseSink, Sink

__version__ = "0.1.0"

__all__ = [
    "Event",
    "EventLevel",
    "utc_now",
    "monotonic_ns",
    "elapsed_ns",
    "Sink",
    "BaseSink",
    "BaseListener",
    "BaseFeed",
    "__version__",
]
