"""Ollama feed with precise timestamps.

Wraps calls to a local/LAN Ollama server (``/api/generate``, ``/api/chat``)
and emits one event per request/response with both clocks stamped and
``duration_ns`` measured across the HTTP call. Ollama returns its own timing
fields (``total_duration``, ``load_duration``, ``prompt_eval_count``,
``eval_count``, ``eval_duration`` — durations in nanoseconds); these are
captured into the payload ALONGSIDE the framework's own monotonic
measurement, so server-reported and client-observed latency can be compared.

Security: an Ollama server is UNAUTHENTICATED. ``OLLAMA_ENDPOINT`` may be a
LAN address but must never be a public interface. The endpoint is not a
secret, but is still kept out of event payloads and log lines.

Emitted event types:
    ``ollama.request``  — before the HTTP call (model, prompt length)
    ``ollama.response`` — after success (server timings + client duration_ns)
    ``ollama.error``    — on transport/API error

Error contract: :meth:`OllamaFeed.generate` / :meth:`OllamaFeed.chat` return
the parsed response dict on success and ``{}`` on any failure (offline
endpoint, HTTP error), emitting ``ollama.error`` and logging a warning —
never raising, per the skip-and-log policy for optional external services.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from ..event import EventLevel, elapsed_ns, monotonic_ns
from ..sink import Sink
from .base import BaseFeed

# Server-side timing fields copied verbatim from an Ollama response into the
# ollama.response payload (durations are nanoseconds; counts are tokens).
_SERVER_TIMING_FIELDS = (
    "total_duration",
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
)


class OllamaFeed(BaseFeed):
    """Instrument Ollama generate/chat calls.

    Args:
        sink: Event destination.
        endpoint: Ollama base URL (default ``OLLAMA_ENDPOINT``).
        model: Default model id (default ``DEFAULT_MODEL``).
        timeout: Per-request HTTP budget in seconds (default ``OLLAMA_TIMEOUT``
            env or 5400 — sized for cold model loads on slow hardware).
    """

    def __init__(
        self,
        sink: Optional[Sink] = None,
        endpoint: str = "",
        model: str = "",
        timeout: float = 0.0,
    ) -> None:
        super().__init__(sink=sink, source="ollama")
        self.endpoint = endpoint or os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434")
        self.model = model or os.getenv("DEFAULT_MODEL", "")
        self.timeout = timeout or float(os.getenv("OLLAMA_TIMEOUT", "5400"))

    def generate(self, prompt: str, **params: Any) -> Dict[str, Any]:
        """Call ``/api/generate``; return the response dict or ``{}`` on error."""
        model = str(params.pop("model", self.model))
        return self._call(
            "generate",
            {"model": model, "prompt": prompt, "stream": False, **params},
            request_payload={"model": model, "prompt_chars": len(prompt)},
        )

    def chat(self, messages: List[Dict[str, str]], **params: Any) -> Dict[str, Any]:
        """Call ``/api/chat``; return the response dict or ``{}`` on error."""
        model = str(params.pop("model", self.model))
        return self._call(
            "chat",
            {"model": model, "messages": messages, "stream": False, **params},
            request_payload={"model": model, "message_count": len(messages)},
        )

    def _call(
        self, api: str, body: Dict[str, Any], request_payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        """POST one non-streaming Ollama request, emitting the event pair."""
        self.emit("ollama.request", message=f"/api/{api} model={body['model']}", **request_payload)
        start = monotonic_ns()
        try:
            response = requests.post(
                f"{self.endpoint.rstrip('/')}/api/{api}",
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data: Dict[str, Any] = response.json()
        except Exception as exc:  # noqa: BLE001 - skip-and-log, never raise
            self.emit(
                "ollama.error",
                message=f"/api/{api} failed: {exc}",
                level=EventLevel.ERROR,
                duration_ns=elapsed_ns(start),
                model=str(body.get("model", "")),
                reason="request_failed",
            )
            print(f"[rfs] WARNING: Ollama call failed ({exc}); returning empty response")
            return {}

        timings = {k: data[k] for k in _SERVER_TIMING_FIELDS if k in data}
        self.emit(
            "ollama.response",
            message=f"/api/{api} model={data.get('model', body['model'])}",
            duration_ns=elapsed_ns(start),
            model=data.get("model", body["model"]),
            done=bool(data.get("done", False)),
            **timings,
        )
        return data
