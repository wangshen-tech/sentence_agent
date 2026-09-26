"""Pieces shared by the engines: recording a turn into the store, and small conversions."""

from __future__ import annotations

import json
from typing import Any

from .. import config
from ..store import Store
from .tools import SAVE_TOOLS

Event = dict[str, Any]

MAX_TOOL_ROUNDS = 10
TOOL_INPUT_EVENT_INTERVAL = 0.08


def jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def parse_result(content: Any) -> Any:
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content


def open_tool_uses(history: list[dict[str, Any]]) -> list[str]:
    """tool_use ids in the last assistant message that never got a tool_result."""
    if not history or history[-1]["role"] != "assistant":
        return []
    return [b["id"] for b in history[-1]["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]


def cancel_results(tool_use_ids: list[str], reason: str) -> list[dict[str, Any]]:
    return [{"type": "tool_result", "tool_use_id": i, "content": reason, "is_error": True} for i in tool_use_ids]


class Turn:
    """One user turn: appends to the in-memory history and mirrors every message into SQLite."""

    def __init__(self, store: Store, conversation_id: int, history: list[dict[str, Any]]):
        self.store = store
        self.conversation_id = conversation_id
        self.history = history
        self.cost = 0.0
        self.context_tokens = 0

    def add_assistant(
        self,
        content: list[dict[str, Any]],
        *,
        model: str,
        stop_reason: str | None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read: int = 0,
        cache_write: int = 0,
    ) -> list[Event]:
        context = input_tokens + cache_read + cache_write
        self.history.append({"role": "assistant", "content": content})
        self.store.append_message(self.conversation_id, "assistant", content, input_tokens=context or None)
        if input_tokens or output_tokens:
            self.store.record_usage(
                self.conversation_id,
                model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read=cache_read,
                cache_write=cache_write,
            )
        self.cost += config.estimate_cost(model, input_tokens, output_tokens, cache_read, cache_write)
        if context:
            self.context_tokens = context
        # Everything streamed so far is now saved; a later retry won't discard it.
        return [{"type": "message_done", "stop_reason": stop_reason}]

    def add_tool_results(self, results: list[dict[str, Any]]) -> list[Event]:
        results = jsonable(results)
        self.history.append({"role": "user", "content": results})
        self.store.append_message(self.conversation_id, "user", results)
        names = {
            b["id"]: b["name"]
            for m in self.history[-2:-1]
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_use"
        }
        events: list[Event] = []
        for result in results:
            tool_use_id = result.get("tool_use_id")
            name = names.get(tool_use_id, "")
            payload = parse_result(result.get("content"))
            if name in SAVE_TOOLS and isinstance(payload, dict) and payload.get("card_id"):
                self.store.set_card_tool_use(int(payload["card_id"]), tool_use_id)
            events.append(
                {"type": "tool_result", "id": tool_use_id, "name": name, "is_error": bool(result.get("is_error")), "result": payload}
            )
        return events

    def close_dangling(self, reason: str) -> None:
        dangling = open_tool_uses(self.history)
        if dangling:
            content = cancel_results(dangling, reason)
            self.store.append_message(self.conversation_id, "user", content)
            self.history.append({"role": "user", "content": content})
