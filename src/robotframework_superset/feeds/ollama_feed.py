"""Ollama feed with precise timestamps.

Wraps calls to a local/LAN Ollama server (``/api/generate``, ``/api/chat``)
and emits one event per request/response with both clocks stamped and
``duration_ns`` measured across the HTTP call. Ollama returns its own timing
fields (``total_duration``, ``load_duration``, ``eval_count``,
``eval_duration`` — all nanoseconds); these are captured into the payload
ALONGSIDE the framework's own monotonic measurement, so server-reported and
client-observed durations can be compared.

Security: an Ollama server is UNAUTHENTICATED. ``OLLAMA_ENDPOINT`` may be a
LAN address but must never be a public interface. The endpoint is not a
secret, but is still kept out of logs at INFO where practical.

Emitted event types:
    ``ollama.request``  — before the HTTP call (model, prompt metadata)
    ``ollama.response`` — after success (server timings + client duration_ns)
    ``ollama.error``    — on error (skip-and-log; never raises)

STATUS: interface skeleton. Bodies raise NotImplementedError until the
"Ollama feed" issue is implemented.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from ..sink import Sink
from .base import BaseFeed


class OllamaFeed(BaseFeed):
    """Instrument Ollama generate/chat calls.

    Args:
        sink: Event destination.
        endpoint: Ollama base URL (default ``OLLAMA_ENDPOINT``).
        model: Default model id (default ``DEFAULT_MODEL``).
    """

    def __init__(
        self,
        sink: Optional[Sink] = None,
        endpoint: str = "",
        model: str = "",
    ) -> None:
        super().__init__(sink=sink, source="ollama")
        self.endpoint = endpoint or os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434")
        self.model = model or os.getenv("DEFAULT_MODEL", "")

    def generate(self, prompt: str, **params: Any) -> Dict[str, Any]:
        """Call ``/api/generate``, emitting request/response/error events.

        Captures Ollama's server-side nanosecond timings into the response
        event's payload alongside the client-measured ``duration_ns``.
        """
        raise NotImplementedError
