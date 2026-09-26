"""Anthropic Messages API engine, driven by the SDK's streaming tool runner.

On the official API with a known Claude model every feature is on: adaptive thinking, effort,
prompt caching, eager tool-input streaming and server-side refusal fallbacks. Behind a relay (custom
base URL) or with an unknown model, only the plain request shape is sent, since proxies commonly
reject fields they don't know.
"""

from __future__ import annotations

import logging
import time
from typing import Any, AsyncIterator

import anthropic
from anthropic import AsyncAnthropic

from .. import config
from ..providers import Provider
from .common import MAX_TOOL_ROUNDS, TOOL_INPUT_EVENT_INTERVAL, Event, Turn, jsonable
from .history import for_anthropic, portable_schema
from .prompts import SYSTEM_PROMPT, system_blocks

log = logging.getLogger(__name__)

MAX_TOKENS = 32_000
COMPAT_MAX_TOKENS = 8_192
MAX_STREAM_RESTARTS = 2
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def serialize_block(block: Any) -> dict[str, Any]:
    """Response content block -> plain dict that can be stored and sent back to the API as-is."""
    if isinstance(block, dict):
        return block
    data = block.model_dump(mode="json", by_alias=True, exclude_none=True)
    data.pop("parsed_output", None)
    return data


class AnthropicEngine:
    def __init__(self, client: AsyncAnthropic, provider: Provider, effort: str):
        self.client = client
        self.provider = provider
        self.effort = effort
        info = config.MODELS_BY_ID.get(provider.model)
        self.full_features = provider.official_anthropic and info is not None
        self.fallbacks = self.full_features and info.server_fallbacks

    def params(self, profile: str) -> dict[str, Any]:
        if not self.full_features:
            return {
                "model": self.provider.model,
                "max_tokens": COMPAT_MAX_TOKENS,
                "system": f"{SYSTEM_PROMPT}\n\n{profile}",
            }
        params: dict[str, Any] = {
            "model": self.provider.model,
            "max_tokens": MAX_TOKENS,
            "system": system_blocks(profile),
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": self.effort},
            # Automatic caching of the growing conversation tail; the system prompt has its own breakpoint.
            "cache_control": {"type": "ephemeral"},
        }
        if self.fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def prepare_tools(self, tools: list[Any]) -> list[Any]:
        if not self.full_features:
            for tool in tools:
                tool.input_schema = portable_schema(tool.input_schema)
        return tools

    async def run(self, turn: Turn, tools: list[Any], profile: str) -> AsyncIterator[Event]:
        params = self.params(profile)
        tools = self.prepare_tools(tools)
        restarts = 0
        try:
            while True:
                runner = self.client.beta.messages.tool_runner(
                    messages=for_anthropic(turn.history, official=self.provider.official_anthropic),
                    tools=tools,
                    stream=True,
                    max_iterations=MAX_TOOL_ROUNDS,
                    **params,
                )
                try:
                    async for stream in runner:
                        async for event in self._stream_events(stream):
                            yield event
                        message = await stream.get_final_message()
                        # Runs this turn's tools (the runner reuses the cached result) and records both halves.
                        # On refusal / max_tokens the runner stops by itself without running any tool.
                        async for event in self._after_message(turn, runner, message):
                            yield event
                    return
                except ValueError:
                    # A streamed tool input that could not be parsed at all. Nothing from the failed attempt
                    # reached the history, so the same request can simply be sent again.
                    restarts += 1
                    if restarts > MAX_STREAM_RESTARTS:
                        raise
                    log.warning("unparseable streamed tool input; retrying (%d)", restarts)
                    yield {"type": "retry"}
        except anthropic.AuthenticationError:
            yield _error("auth", "API key 无效或已被撤销。去「设置」里检查这个服务商的 key。")
        except anthropic.PermissionDeniedError as e:
            yield _error("permission", f"这个 API key 没有权限：{e.message}")
        except anthropic.NotFoundError:
            yield _error("model", self._not_found_hint())
        except anthropic.RateLimitError:
            yield _error("rate_limit", "请求太频繁，或者账户余额/额度用完了。稍等一下再试，或去服务商那里看看额度。")
        except anthropic.BadRequestError as e:
            yield _error("bad_request", f"请求被拒绝：{e.message}")
        except anthropic.APIStatusError as e:
            yield _error("server", f"服务暂时出错（{e.status_code}）：{e.message}")
        except anthropic.APIConnectionError:
            where = self.provider.base_url or "Claude"
            yield _error("network", f"连不上 {where}。检查一下网络或接口地址再试。")
        except ValueError:
            yield _error("format", "这次生成的内容格式有问题，再发一次试试。")

    def _not_found_hint(self) -> str:
        if self.provider.official_anthropic:
            return f"找不到模型 {self.provider.model}。去「设置」里换一个模型。"
        return f"找不到模型 {self.provider.model}，或者接口地址不对。Anthropic 格式的地址一般不带 /v1。"

    async def _stream_events(self, stream: Any) -> AsyncIterator[Event]:
        tool: dict[str, str] | None = None
        last_input_event = 0.0
        async for event in stream:
            kind = event.type
            if kind == "content_block_start":
                block = event.content_block
                if block.type == "tool_use":
                    tool = {"id": block.id, "name": block.name}
                    yield {"type": "tool_start", **tool}
                elif block.type == "thinking":
                    yield {"type": "thinking_start"}
                elif block.type == "text":
                    yield {"type": "text_start"}
            elif kind == "thinking":
                if event.thinking:
                    yield {"type": "thinking_delta", "text": event.thinking}
            elif kind == "text":
                yield {"type": "text_delta", "text": event.text}
            elif kind == "input_json" and tool is not None:
                now = time.monotonic()
                if now - last_input_event >= TOOL_INPUT_EVENT_INTERVAL:
                    last_input_event = now
                    yield {"type": "tool_input", **tool, "input": jsonable(event.snapshot)}
            elif kind == "content_block_stop":
                block = event.content_block
                if block.type == "tool_use":
                    yield {"type": "tool_input", "id": block.id, "name": block.name, "input": jsonable(block.input), "final": True}
                    tool = None

    async def _after_message(self, turn: Turn, runner: Any, message: Any) -> AsyncIterator[Event]:
        content = [serialize_block(b) for b in message.content]
        usage = message.usage
        for event in turn.add_assistant(
            content,
            model=message.model,
            stop_reason=message.stop_reason,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            cache_read=usage.cache_read_input_tokens or 0,
            cache_write=usage.cache_creation_input_tokens or 0,
        ):
            yield event

        if any(b.get("type") == "fallback" for b in content):
            yield {"type": "notice", "level": "info", "text": f"这一轮由 {message.model} 接手回答。"}
        if message.stop_reason == "refusal":
            yield {"type": "notice", "level": "warn", "text": "模型没有回答这个请求。换个说法再试试。"}
            return
        if message.stop_reason == "max_tokens":
            yield {"type": "notice", "level": "warn", "text": "回答太长被截断了。可以让它接着说，或者把问题拆小一点。"}
            return
        if message.stop_reason == "model_context_window_exceeded":
            yield {"type": "notice", "level": "warn", "text": "这段对话太长了，新开一个对话再继续吧。"}
            return
        if message.stop_reason != "tool_use":
            return

        response = await runner.generate_tool_call_response()
        if response is None:
            return
        for event in turn.add_tool_results(response["content"]):
            yield event


def _error(code: str, message: str) -> Event:
    return {"type": "error", "code": code, "message": message}
