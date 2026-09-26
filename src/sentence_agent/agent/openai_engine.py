"""OpenAI-compatible Chat Completions engine: OpenAI, DeepSeek, Qwen, Kimi, GLM, OpenRouter, Gemini's
compatibility endpoint, and most API relays.

The loop is written out here because these endpoints differ in small ways the code has to tolerate:
some reject `stream_options`, some send reasoning in a non-standard field, some omit tool-call ids.
Tool inputs are validated by the same Pydantic-backed tool objects the Anthropic engine uses.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, AsyncIterator

import jiter
import openai
from openai import AsyncOpenAI

from ..providers import Provider
from .common import MAX_TOOL_ROUNDS, TOOL_INPUT_EVENT_INTERVAL, Event, Turn
from .history import portable_schema, to_openai
from .prompts import SYSTEM_PROMPT


def _partial_json(raw: str) -> Any:
    if not raw:
        return {}
    try:
        return jiter.from_json(raw.encode(), partial_mode="trailing-strings")
    except ValueError:
        return None


def _reasoning_delta(delta: Any) -> str:
    extra = delta.model_extra or {}
    value = extra.get("reasoning_content") or extra.get("reasoning")
    return value if isinstance(value, str) else ""


def _tool_error_text(exc: Exception) -> str:
    cause = exc.__cause__ or exc
    return f"Error: {cause}"


class OpenAIEngine:
    def __init__(self, client: AsyncOpenAI, provider: Provider):
        self.client = client
        self.provider = provider
        self._stream_options_ok = True

    async def run(self, turn: Turn, tools: list[Any], profile: str) -> AsyncIterator[Event]:
        by_name = {t.name: t for t in tools}
        functions = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": portable_schema(t.input_schema)}}
            for t in tools
        ]
        system = f"{SYSTEM_PROMPT}\n\n{profile}"
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                stream = await self._open(model=self.provider.model, messages=to_openai(turn.history, system), tools=functions)
                result = _RoundState()
                async for event in self._consume(stream, result):
                    yield event

                content = result.content()
                if not content:
                    yield {"type": "notice", "level": "warn", "text": "模型这次什么也没返回。再发一次试试，或者换一个模型。"}
                    return
                for block in content:
                    if block["type"] == "tool_use":
                        yield {"type": "tool_input", "id": block["id"], "name": block["name"], "input": block["input"], "final": True}
                for event in turn.add_assistant(content, model=self.provider.model, stop_reason=result.finish, **result.usage()):
                    yield event

                if result.finish == "length":
                    yield {"type": "notice", "level": "warn", "text": "回答太长被截断了。可以让它接着说，或者把问题拆小一点。"}
                    return
                if result.finish == "content_filter":
                    yield {"type": "notice", "level": "warn", "text": "服务商的内容审核拦下了这次回答。换个说法再试试。"}
                    return
                calls = [b for b in content if b["type"] == "tool_use"]
                if not calls:
                    return
                results = [await self._run_tool(by_name, block, result) for block in calls]
                for event in turn.add_tool_results(results):
                    yield event
            yield {"type": "notice", "level": "warn", "text": "这一轮调用工具的次数太多，先停下了。"}
        except openai.AuthenticationError:
            yield _error("auth", "API key 无效或已过期。去「设置」里检查这个服务商的 key。")
        except openai.PermissionDeniedError as e:
            yield _error("permission", f"这个 key 没有权限：{_message(e)}")
        except openai.NotFoundError as e:
            yield _error("model", f"找不到模型 {self.provider.model}，或者接口地址不对（很多服务商的地址要以 /v1 结尾）。{_message(e)}")
        except openai.RateLimitError as e:
            yield _error("rate_limit", f"请求太频繁，或者余额/额度用完了。{_message(e)}")
        except openai.BadRequestError as e:
            yield _error("bad_request", f"请求被拒绝：{_message(e)}　如果提示和 tools 有关，说明这个模型不支持工具调用，换一个模型试试。")
        except openai.APIStatusError as e:
            hint = "账户余额不足。" if e.status_code == 402 else ""
            yield _error("server", f"服务出错（{e.status_code}）。{hint}{_message(e)}")
        except openai.APIConnectionError:
            yield _error("network", f"连不上 {self.provider.base_url}。检查一下网络或接口地址再试。")

    async def _open(self, **kwargs: Any) -> Any:
        if self._stream_options_ok:
            try:
                return await self.client.chat.completions.create(**kwargs, stream=True, stream_options={"include_usage": True})
            except openai.BadRequestError:
                # Some compatible endpoints reject stream_options; usage is optional, so ask again without it.
                self._stream_options_ok = False
        return await self.client.chat.completions.create(**kwargs, stream=True)

    async def _consume(self, stream: Any, state: "_RoundState") -> AsyncIterator[Event]:
        last_input_event = 0.0
        async for chunk in stream:
            if chunk.usage:
                state.usage_data = chunk.usage
            for choice in chunk.choices or []:
                delta = choice.delta
                reasoning = _reasoning_delta(delta) if delta else ""
                if reasoning:
                    if not state.reasoning:
                        yield {"type": "thinking_start"}
                    state.reasoning += reasoning
                    yield {"type": "thinking_delta", "text": reasoning}
                if delta and delta.content:
                    if not state.text:
                        yield {"type": "text_start"}
                    state.text += delta.content
                    yield {"type": "text_delta", "text": delta.content}
                for call in (delta.tool_calls if delta else None) or []:
                    slot = state.calls.setdefault(call.index, {"id": "", "name": "", "args": "", "announced": False})
                    if call.id and not slot["announced"]:
                        slot["id"] = call.id
                    if call.function and call.function.name and not slot["name"]:
                        slot["name"] = call.function.name
                    if call.function and call.function.arguments:
                        slot["args"] += call.function.arguments
                    if slot["name"] and not slot["announced"]:
                        slot["id"] = slot["id"] or f"call_{secrets.token_hex(6)}"
                        slot["announced"] = True
                        yield {"type": "tool_start", "id": slot["id"], "name": slot["name"]}
                    now = time.monotonic()
                    if slot["announced"] and now - last_input_event >= TOOL_INPUT_EVENT_INTERVAL:
                        partial = _partial_json(slot["args"])
                        if isinstance(partial, dict):
                            last_input_event = now
                            yield {"type": "tool_input", "id": slot["id"], "name": slot["name"], "input": partial}
                if choice.finish_reason:
                    state.finish = choice.finish_reason

    async def _run_tool(self, by_name: dict[str, Any], block: dict[str, Any], state: "_RoundState") -> dict[str, Any]:
        tool = by_name.get(block["name"])
        if tool is None:
            return {"type": "tool_result", "tool_use_id": block["id"], "is_error": True, "content": f"Error: no tool named {block['name']}"}
        if block["id"] in state.invalid_ids:
            return {
                "type": "tool_result",
                "tool_use_id": block["id"],
                "is_error": True,
                "content": json.dumps({"INVALID_JSON": state.raw_args.get(block["id"], "")}, ensure_ascii=False),
            }
        try:
            output = await tool.call(block["input"])
        except Exception as exc:  # noqa: BLE001 - the model gets the error and can correct itself
            return {"type": "tool_result", "tool_use_id": block["id"], "is_error": True, "content": _tool_error_text(exc)}
        return {"type": "tool_result", "tool_use_id": block["id"], "content": output}


class _RoundState:
    def __init__(self) -> None:
        self.text = ""
        self.reasoning = ""
        self.calls: dict[int, dict[str, Any]] = {}
        self.finish: str | None = None
        self.usage_data: Any = None
        self.raw_args: dict[str, str] = {}
        self.invalid_ids: set[str] = set()

    def content(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if self.reasoning:
            blocks.append({"type": "reasoning", "text": self.reasoning})
        if self.text:
            blocks.append({"type": "text", "text": self.text})
        for index in sorted(self.calls):
            slot = self.calls[index]
            if not slot["name"]:
                continue
            tool_id = slot["id"] or f"call_{secrets.token_hex(6)}"
            self.raw_args[tool_id] = slot["args"]
            try:
                parsed = json.loads(slot["args"] or "{}")
                invalid = not isinstance(parsed, dict)
            except json.JSONDecodeError:
                parsed, invalid = {}, True
            if invalid:
                self.invalid_ids.add(tool_id)
            blocks.append({"type": "tool_use", "id": tool_id, "name": slot["name"], "input": parsed if not invalid else {}})
        return blocks

    def usage(self) -> dict[str, int]:
        u = self.usage_data
        if u is None:
            return {}
        prompt = u.prompt_tokens or 0
        details = getattr(u, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        return {"input_tokens": max(prompt - cached, 0), "cache_read": cached, "output_tokens": u.completion_tokens or 0}


def _message(e: openai.APIStatusError) -> str:
    body = e.body
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
    return e.message


def _error(code: str, message: str) -> Event:
    return {"type": "error", "code": code, "message": message}
