"""OpenAI (and OpenAI-compatible) feed with precise timestamps.

Wraps Chat Completions calls to OpenAI, Azure OpenAI, or any compatible API
(Together, Groq, Fireworks, ...) and emits one event per request/response
with both clocks stamped and ``duration_ns`` measured across the HTTP call.

Emitted event types:
    ``openai.request``  — just before the HTTP call (prompt metadata)
    ``openai.response`` — after a successful response (usage, finish_reason,
                          duration_ns, model id)
    ``openai.error``    — on transport/API error (skip-and-log; never raises)

Secrets: ``OPENAI_API_KEY`` is read from the environment and NEVER placed in
an event payload. If the key is absent the feed skips-and-logs rather than
failing, consistent with optional-dependency handling.

STATUS: interface skeleton. Bodies raise NotImplementedError until the
"OpenAI feed" issue is implemented.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from ..sink import Sink
from .base import BaseFeed


class OpenAIFeed(BaseFeed):
    """Instrument OpenAI-compatible chat completions.

    Args:
        sink: Event destination.
        base_url: API base (default ``OPENAI_BASE_URL`` or the OpenAI URL).
        model: Default model id for requests.
    """

    def __init__(
        self,
        sink: Optional[Sink] = None,
        base_url: str = "",
        model: str = "",
    ) -> None:
        super().__init__(sink=sink, source="openai")
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.model = model
        self._api_key = os.getenv("OPENAI_API_KEY", "")

    def chat(self, messages: list[dict[str, str]], **params: Any) -> Dict[str, Any]:
        """Call chat completions, emitting request/response/error events.

        Returns the parsed response dict. Duration is measured with monotonic
        clocks around the HTTP call.
        """
        raise NotImplementedError
