"""Turn the stored API history of a conversation into the items the chat view renders.

An assistant "turn" in the UI spans several API messages: assistant (tool calls) -> user (tool
results) -> assistant (reply) ... They are merged into one item with an ordered list of blocks.
"""

from __future__ import annotations

from typing import Any

from ..store import Store
from .session import _parse_result
from .tools import SAVE_TOOLS


def build_transcript(store: Store, conversation_id: int) -> list[dict[str, Any]]:
    rows = store.load_history_rows(conversation_id)
    tool_ids = [
        b["id"]
        for r in rows
        if r["role"] == "assistant"
        for b in r["content"]
        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in SAVE_TOOLS
    ]
    cards = store.cards_by_tool_use(tool_ids)

    items: list[dict[str, Any]] = []
    turn: dict[str, Any] | None = None
    tools_by_id: dict[str, dict[str, Any]] = {}

    for row in rows:
        content = row["content"]
        if row["role"] == "user":
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tool = tools_by_id.get(b.get("tool_use_id", ""))
                    if tool is not None:
                        tool["result"] = _parse_result(b.get("content"))
                        tool["is_error"] = bool(b.get("is_error"))
            if texts:
                turn = None
                items.append({"role": "user", "id": row["id"], "text": "\n".join(texts), "created_at": row["created_at"]})
            continue

        if turn is None:
            turn = {"role": "assistant", "id": row["id"], "blocks": [], "created_at": row["created_at"]}
            items.append(turn)
        for b in content:
            if not isinstance(b, dict):
                continue
            kind = b.get("type")
            if kind == "thinking" and b.get("thinking"):
                turn["blocks"].append({"type": "thinking", "text": b["thinking"]})
            elif kind == "text" and b.get("text"):
                turn["blocks"].append({"type": "text", "text": b["text"]})
            elif kind == "tool_use":
                tool = {"type": "tool", "id": b["id"], "name": b["name"], "input": b.get("input") or {}}
                if b["name"] in SAVE_TOOLS:
                    card = cards.get(b["id"])
                    tool["card"] = (
                        {"id": card["id"], "en": card["en"], "zh": card["zh"], "level": card["level"]} if card else None
                    )
                tools_by_id[b["id"]] = tool
                turn["blocks"].append(tool)
            elif kind == "fallback":
                turn["blocks"].append({"type": "notice", "text": "这一轮由备用模型接手回答。"})
    return items
