"""OpenAI (and OpenAI-compatible) feed with precise timestamps.

Wraps Chat Completions calls to OpenAI, Azure OpenAI, or any compatible API
(Together, Groq, Fireworks, ...) and emits one event per request/response
with both clocks stamped and ``duration_ns`` measured across the HTTP call.

Emitted event types:
    ``openai.request``  — just before the HTTP call (model, message count,
                          params — never message content, never the key)
    ``openai.response`` — after a successful response (usage, finish_reason,
                          duration_ns, model id, server latency header)
    ``openai.error``    — missing key or transport/API error

Error contract: :meth:`OpenAIFeed.chat` returns the parsed response dict on
success and ``{}`` on any failure — an absent ``OPENAI_API_KEY`` (the
optional-dependency pattern: skip-and-log, no HTTP call attempted) or a
transport/API error (``openai.error`` emitted with the client-side
``duration_ns``). It never raises, so instrumentation can be added to a
run without introducing a new failure mode.

Secrets: ``OPENAI_API_KEY`` is read from the environment and NEVER placed in
an event payload, message, or log line.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from ..event import EventLevel, elapsed_ns, monotonic_ns
from ..sink import Sink
from .base import BaseFeed


class OpenAIFeed(BaseFeed):
    """Instrument OpenAI-compatible chat completions.

    Args:
        sink: Event destination.
        base_url: API base (default ``OPENAI_BASE_URL`` or the OpenAI URL).
        model: Default model id for requests.
        timeout: Per-request HTTP budget in seconds.
    """

    def __init__(
        self,
        sink: Optional[Sink] = None,
        base_url: str = "",
        model: str = "",
        timeout: float = 120.0,
    ) -> None:
        super().__init__(sink=sink, source="openai")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = model
        self.timeout = timeout
        self._api_key = os.getenv("OPENAI_API_KEY", "")

    def chat(self, messages: List[Dict[str, str]], **params: Any) -> Dict[str, Any]:
        """Call chat completions, emitting request/response/error events.

        Returns the parsed response dict, or ``{}`` on any failure (see the
        module docstring for the full error contract). ``model`` may be
        overridden per call via ``params``.
        """
        model = str(params.pop("model", self.model))
        if not self._api_key:
            self.emit(
                "openai.error",
                message="OPENAI_API_KEY is not set; skipping call",
                level=EventLevel.WARN,
                model=model,
                reason="missing_api_key",
            )
            print("[rfs] WARNING: OPENAI_API_KEY is not set; OpenAI call skipped")
            return {}

        self.emit(
            "openai.request",
            message=f"chat/completions model={model}",
            model=model,
            message_count=len(messages),
            params={k: v for k, v in params.items() if _is_scalar(v)},
        )
        start = monotonic_ns()
        try:
            response = requests.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                json={"model": model, "messages": messages, **params},
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self.timeout,
            )
            response.raise_for_status()
            data: Dict[str, Any] = response.json()
        except Exception as exc:  # noqa: BLE001 - skip-and-log, never raise
            self.emit(
                "openai.error",
                message=f"chat/completions failed: {exc}",
                level=EventLevel.ERROR,
                duration_ns=elapsed_ns(start),
                model=model,
                reason="request_failed",
            )
            print(f"[rfs] WARNING: OpenAI call failed ({exc}); returning empty response")
            return {}

        choices = data.get("choices") or [{}]
        self.emit(
            "openai.response",
            message=f"chat/completions model={data.get('model', model)}",
            duration_ns=elapsed_ns(start),
            model=data.get("model", model),
            usage=data.get("usage", {}),
            finish_reason=choices[0].get("finish_reason", ""),
            server_processing_ms=_int_header(response.headers, "openai-processing-ms"),
        )
        return data


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool)) or value is None


def _int_header(headers: Any, name: str) -> int:
    try:
        return int(headers.get(name, -1))
    except (TypeError, ValueError):
        return -1
