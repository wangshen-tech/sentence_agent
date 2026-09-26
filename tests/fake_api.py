"""A scripted stand-in for the Messages API, so the real SDK tool runner can be exercised offline."""

from __future__ import annotations

import json
from typing import Any

import httpx2
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient


def _sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events).encode()


def message_start(input_tokens: int = 1200) -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": 1,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        },
    }


def thinking_block(index: int, text: str) -> list[dict[str, Any]]:
    return [
        {"type": "content_block_start", "index": index, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": index, "delta": {"type": "thinking_delta", "thinking": text}},
        {"type": "content_block_delta", "index": index, "delta": {"type": "signature_delta", "signature": "sig-abc"}},
        {"type": "content_block_stop", "index": index},
    ]


def text_block(index: int, *chunks: str) -> list[dict[str, Any]]:
    return [
        {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}},
        *[{"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": c}} for c in chunks],
        {"type": "content_block_stop", "index": index},
    ]


def tool_block(index: int, tool_id: str, name: str, tool_input: dict[str, Any], pieces: int = 3) -> list[dict[str, Any]]:
    raw = json.dumps(tool_input, ensure_ascii=False)
    size = max(1, len(raw) // pieces + 1)
    parts = [raw[i : i + size] for i in range(0, len(raw), size)]
    return [
        {"type": "content_block_start", "index": index, "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": {}}},
        *[{"type": "content_block_delta", "index": index, "delta": {"type": "input_json_delta", "partial_json": p}} for p in parts],
        {"type": "content_block_stop", "index": index},
    ]


def finish(stop_reason: str, output_tokens: int = 300) -> list[dict[str, Any]]:
    return [
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": output_tokens}},
        {"type": "message_stop"},
    ]


class FakeAPI:
    """Replays one scripted response per /v1/messages request and records what was sent."""

    def __init__(self, script: list[list[dict[str, Any]] | httpx2.Response]):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(dict(request.headers))
        step = self.script.pop(0)
        if isinstance(step, httpx2.Response):
            return step
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(step))

    def client(self) -> AsyncAnthropic:
        transport = httpx2.MockTransport(self.handler)
        return AsyncAnthropic(api_key="sk-ant-test", max_retries=0, http_client=DefaultAsyncHttpxClient(transport=transport))
