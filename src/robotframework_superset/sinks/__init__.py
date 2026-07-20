"""Sink implementations."""

from __future__ import annotations

from .gelf import GelfSink
from .multi import MultiSink
from .null import MemorySink, NullSink

__all__ = ["GelfSink", "MemorySink", "MultiSink", "NullSink"]
