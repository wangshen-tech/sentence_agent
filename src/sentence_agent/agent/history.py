"""The stored conversation history, adapted to each API format.

History is always stored as Anthropic-style content blocks (text, thinking, tool_use, tool_result),
whichever provider produced it. Reasoning text from other providers is stored as a "reasoning"
block, which no API ever receives back. These helpers turn that one history into a valid request for
whichever provider answers next, so a conversation can move between providers.
"""

from __future__ import annotations

import json
from typing import Any

Message = dict[str, Any]

_ANTHROPIC_ONLY = {"thinking", "redacted_thinking", "fallback"}
_NEVER_SENT = {"reasoning"}


def for_anthropic(history: list[Message], official: bool) -> list[Message]:
    """History for an Anthropic Messages request.

    The official API gets its own thinking blocks back (their signatures must round-trip). A relay
    or proxy gets none of them: it may be forwarding to a model that can't read them.
    """
    drop = _NEVER_SENT if official else _NEVER_SENT | _ANTHROPIC_ONLY
    out: list[Message] = []
    for message in history:
        content = [b for b in message["content"] if not (isinstance(b, dict) and b.get("type") in drop)]
        content = [b for b in content if not (isinstance(b, dict) and b.get("type") == "text" and not b.get("text"))]
        if content:
            out.append({"role": message["role"], "content": content})
    return out


def _tool_result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return json.dumps(content, ensure_ascii=False)


def to_openai(history: list[Message], system: str) -> list[dict[str, Any]]:
    """History as Chat Completions messages: tool_use -> tool_calls, tool_result -> role "tool"."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in history:
        blocks = [b for b in message["content"] if isinstance(b, dict)]
        if message["role"] == "user":
            for b in blocks:
                if b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": _tool_result_text(b.get("content"))})
            text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text" and b.get("text"))
            if text:
                out.append({"role": "user", "content": text})
            continue
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        calls = [
            {
                "id": b["id"],
                "type": "function",
                "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)},
            }
            for b in blocks
            if b.get("type") == "tool_use"
        ]
        if not text and not calls:
            continue
        entry: dict[str, Any] = {"role": "assistant", "content": text or None}
        if calls:
            entry["tool_calls"] = calls
        out.append(entry)
    return out


_SCHEMA_KEYS = {"type", "properties", "required", "items", "description", "enum"}


def portable_schema(schema: Any) -> Any:
    """A plain JSON Schema subset that every OpenAI-compatible provider and relay accepts.

    Drops pydantic extras (title, default, additionalProperties) and turns `X | None` into just `X`,
    since some providers (Gemini's compatibility layer, older relays) reject anyOf/null.
    """
    if isinstance(schema, list):
        return [portable_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if not (isinstance(o, dict) and o.get("type") == "null")]
        merged = {**(options[0] if options else {}), **{k: v for k, v in schema.items() if k != "anyOf"}}
        return portable_schema(merged)
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "properties":
            out[key] = {name: portable_schema(sub) for name, sub in value.items()}
        elif key == "items":
            out[key] = portable_schema(value)
        else:
            out[key] = value
    return out
