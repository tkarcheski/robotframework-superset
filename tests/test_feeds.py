"""Tests for the OpenAI and Ollama feeds (HTTP layer mocked)."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pytest

from robotframework_superset.event import Event
from robotframework_superset.feeds import ollama_feed, openai_feed
from robotframework_superset.feeds.ollama_feed import OllamaFeed
from robotframework_superset.feeds.openai_feed import OpenAIFeed
from robotframework_superset.sinks.null import MemorySink


class _FakeResponse:
    def __init__(self, payload: Dict[str, Any], status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.headers: Dict[str, str] = {"openai-processing-ms": "321"}

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakePost:
    """Records calls; returns a canned response or raises."""

    def __init__(self, payload: Optional[Dict[str, Any]] = None, exc: Optional[Exception] = None):
        self.payload = payload or {}
        self.exc = exc
        self.calls: List[Dict[str, Any]] = []

    def __call__(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if self.exc is not None:
            raise self.exc
        return _FakeResponse(self.payload)


def _events_of(sink: MemorySink, event_type: str) -> List[Event]:
    return [e for e in sink.events if e.event_type == event_type]


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

_OPENAI_RESPONSE = {
    "id": "chatcmpl-1",
    "model": "gpt-4o-2024-08-06",
    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


def test_openai_chat_emits_request_and_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-test-key")
    fake = _FakePost(_OPENAI_RESPONSE)
    monkeypatch.setattr(openai_feed.requests, "post", fake)
    sink = MemorySink()
    feed = OpenAIFeed(sink=sink, model="gpt-4o")

    result = feed.chat([{"role": "user", "content": "hello"}], temperature=0)

    assert result["choices"][0]["message"]["content"] == "hi"
    (req,) = _events_of(sink, "openai.request")
    assert req.payload["model"] == "gpt-4o"
    assert req.payload["message_count"] == 1
    (resp,) = _events_of(sink, "openai.response")
    assert resp.duration_ns >= 0
    assert resp.payload["usage"]["total_tokens"] == 7
    assert resp.payload["finish_reason"] == "stop"
    assert resp.payload["model"] == "gpt-4o-2024-08-06"
    assert resp.payload["server_processing_ms"] == 321
    # The request actually carried the bearer token…
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer sk-secret-test-key"


def test_openai_key_never_in_events_or_payloads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-test-key")
    fake = _FakePost(_OPENAI_RESPONSE)
    monkeypatch.setattr(openai_feed.requests, "post", fake)
    sink = MemorySink()
    OpenAIFeed(sink=sink, model="gpt-4o").chat([{"role": "user", "content": "hello"}])
    for event in sink.events:
        assert "sk-secret-test-key" not in json.dumps(event.to_dict())


def test_openai_missing_key_skips_and_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fake = _FakePost(_OPENAI_RESPONSE)
    monkeypatch.setattr(openai_feed.requests, "post", fake)
    sink = MemorySink()
    result = OpenAIFeed(sink=sink, model="gpt-4o").chat([{"role": "user", "content": "x"}])
    assert result == {}
    assert fake.calls == []  # no HTTP call attempted
    assert _events_of(sink, "openai.error")
    assert "WARNING" in capsys.readouterr().out


def test_openai_transport_error_emits_error_and_returns_empty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-test-key")
    monkeypatch.setattr(openai_feed.requests, "post", _FakePost(exc=ConnectionError("refused")))
    sink = MemorySink()
    result = OpenAIFeed(sink=sink, model="gpt-4o").chat([{"role": "user", "content": "x"}])
    assert result == {}
    (err,) = _events_of(sink, "openai.error")
    assert err.duration_ns >= 0
    assert "refused" in err.message
    assert "WARNING" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------

_OLLAMA_RESPONSE = {
    "model": "qwen3:8b",
    "response": "4",
    "done": True,
    "total_duration": 5_000_000_000,
    "load_duration": 1_000_000_000,
    "prompt_eval_count": 12,
    "eval_count": 3,
    "eval_duration": 2_000_000_000,
}


def test_ollama_generate_captures_server_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePost(_OLLAMA_RESPONSE)
    monkeypatch.setattr(ollama_feed.requests, "post", fake)
    sink = MemorySink()
    feed = OllamaFeed(sink=sink, endpoint="http://localhost:11434", model="qwen3:8b")

    result = feed.generate("What is 2+2?")

    assert result["response"] == "4"
    assert fake.calls[0]["url"] == "http://localhost:11434/api/generate"
    assert fake.calls[0]["json"]["stream"] is False
    (req,) = _events_of(sink, "ollama.request")
    assert req.payload["model"] == "qwen3:8b"
    (resp,) = _events_of(sink, "ollama.response")
    # Client-observed duration AND server-reported ns timings, side by side.
    assert resp.duration_ns >= 0
    assert resp.payload["total_duration"] == 5_000_000_000
    assert resp.payload["load_duration"] == 1_000_000_000
    assert resp.payload["prompt_eval_count"] == 12
    assert resp.payload["eval_count"] == 3
    assert resp.payload["eval_duration"] == 2_000_000_000


def test_ollama_chat_uses_chat_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakePost({"model": "qwen3:8b", "message": {"role": "assistant", "content": "hi"}, "done": True})
    monkeypatch.setattr(ollama_feed.requests, "post", fake)
    sink = MemorySink()
    feed = OllamaFeed(sink=sink, endpoint="http://localhost:11434", model="qwen3:8b")
    result = feed.chat([{"role": "user", "content": "hello"}])
    assert result["message"]["content"] == "hi"
    assert fake.calls[0]["url"] == "http://localhost:11434/api/chat"
    assert _events_of(sink, "ollama.response")


def test_ollama_offline_endpoint_skips_and_logs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ollama_feed.requests, "post", _FakePost(exc=ConnectionError("refused")))
    sink = MemorySink()
    feed = OllamaFeed(sink=sink, endpoint="http://localhost:11434", model="qwen3:8b")
    result = feed.generate("hello?")
    assert result == {}
    (err,) = _events_of(sink, "ollama.error")
    assert "refused" in err.message
    assert "WARNING" in capsys.readouterr().out


def test_feed_events_validate_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ollama_feed.requests, "post", _FakePost(_OLLAMA_RESPONSE))
    sink = MemorySink()
    OllamaFeed(sink=sink, endpoint="http://x", model="m").generate("q")
    for event in sink.events:
        event.validate()
