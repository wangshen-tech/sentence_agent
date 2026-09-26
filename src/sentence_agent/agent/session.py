"""One user turn through the Claude tool runner, streamed to the UI as small JSON events.

The conversation history is mirrored into SQLite as it grows (append-only, full content blocks, so
thinking signatures and fallback blocks round-trip unchanged). That lets a conversation resume after
the app restarts, and lets a failed turn be retried from exactly where it stopped.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, AsyncIterator, Callable

import anthropic
from anthropic import AsyncAnthropic

from .. import config, credentials
from ..store import Store
from .prompts import learner_profile, system_blocks
from .tools import SAVE_TOOLS, ToolContext, build_tools

log = logging.getLogger(__name__)

MAX_TOKENS = 32_000
MAX_TOOL_ROUNDS = 10
MAX_STREAM_RESTARTS = 2
TOOL_INPUT_EVENT_INTERVAL = 0.08
FALLBACK_BETA = "server-side-fallback-2026-07-01"

Event = dict[str, Any]


class MissingKeyError(RuntimeError):
    pass


def serialize_block(block: Any) -> dict[str, Any]:
    """Response content block -> plain dict that can be stored and sent back to the API as-is."""
    if isinstance(block, dict):
        return block
    data = block.model_dump(mode="json", by_alias=True, exclude_none=True)
    data.pop("parsed_output", None)
    return data


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _open_tool_uses(history: list[dict[str, Any]]) -> list[str]:
    """tool_use ids in the last assistant message that never got a tool_result."""
    if not history or history[-1]["role"] != "assistant":
        return []
    return [b["id"] for b in history[-1]["content"] if isinstance(b, dict) and b.get("type") == "tool_use"]


def _cancel_results(tool_use_ids: list[str], reason: str) -> list[dict[str, Any]]:
    return [{"type": "tool_result", "tool_use_id": i, "content": reason, "is_error": True} for i in tool_use_ids]


class AgentService:
    def __init__(self, store: Store, client_factory: Callable[[], AsyncAnthropic] | None = None):
        self.store = store
        self._client_factory = client_factory
        self._client: AsyncAnthropic | None = None
        self._client_key: str | None = None

    # ---------- configuration ----------

    def client(self) -> AsyncAnthropic:
        if self._client_factory is not None:
            return self._client_factory()
        key = credentials.get_api_key()
        if not key:
            raise MissingKeyError("还没有设置 API key")
        if self._client is None or key != self._client_key:
            self._client = AsyncAnthropic(api_key=key, max_retries=2)
            self._client_key = key
        return self._client

    def model(self) -> str:
        model = self.store.get_setting("model", config.DEFAULT_MODEL) or config.DEFAULT_MODEL
        return model if model in config.MODELS_BY_ID else config.DEFAULT_MODEL

    def effort(self) -> str:
        effort = self.store.get_setting("effort", config.DEFAULT_EFFORT) or config.DEFAULT_EFFORT
        return effort if effort in config.EFFORT_IDS else config.DEFAULT_EFFORT

    def request_params(self, profile: str) -> dict[str, Any]:
        model = self.model()
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": MAX_TOKENS,
            "system": system_blocks(profile),
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": self.effort()},
            # Automatic caching of the growing conversation tail; the system prompt has its own breakpoint.
            "cache_control": {"type": "ephemeral"},
        }
        if config.MODELS_BY_ID[model].server_fallbacks:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    # ---------- conversations ----------

    def new_conversation(self) -> int:
        profile = learner_profile(self.store.top_notes(8), self.store.stats())
        return self.store.create_conversation(profile=profile)

    def current_conversation(self) -> int:
        """Reuse the most recent conversation if it was active today, otherwise start a fresh one."""
        latest = self.store.latest_conversation()
        today = time.localtime()
        if latest is not None:
            last = time.localtime(latest["updated_at"])
            if (last.tm_year, last.tm_yday) == (today.tm_year, today.tm_yday):
                return latest["id"]
        return self.new_conversation()

    def _load_history(self, conversation_id: int) -> list[dict[str, Any]]:
        history = self.store.load_history(conversation_id)
        dangling = _open_tool_uses(history)
        if dangling:
            # The app was closed or the turn was stopped between a tool call and its result.
            content = _cancel_results(dangling, "这次调用被中断了，没有执行。")
            self.store.append_message(conversation_id, "user", content)
            history.append({"role": "user", "content": content})
        return history

    # ---------- the turn ----------

    async def run_turn(self, conversation_id: int, text: str) -> AsyncIterator[Event]:
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            yield {"type": "error", "code": "not_found", "message": "找不到这段对话"}
            return

        history = self._load_history(conversation_id)
        user_content = [{"type": "text", "text": text}]
        self.store.append_message(conversation_id, "user", user_content)
        history.append({"role": "user", "content": user_content})
        if not conv["title"]:
            self.store.set_conversation_title(conversation_id, text.strip().splitlines()[0][:40])

        totals = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "cost": 0.0, "context": 0}
        try:
            client = self.client()
            ctx = ToolContext(self.store, conversation_id)
            tools = build_tools(ctx)
            params = self.request_params(conv["profile"])
            yield {"type": "turn_start", "model": params["model"]}

            restarts = 0
            while True:
                runner = client.beta.messages.tool_runner(
                    messages=history, tools=tools, stream=True, max_iterations=MAX_TOOL_ROUNDS, **params
                )
                try:
                    async for stream in runner:
                        async for event in self._stream_events(stream):
                            yield event
                        message = await stream.get_final_message()
                        # Runs this turn's tools (the runner reuses the cached result) and records both halves.
                        # On refusal / max_tokens the runner stops by itself without running any tool.
                        async for event in self._after_message(conversation_id, history, runner, message, totals):
                            yield event
                    break
                except ValueError:
                    # A streamed tool input that could not be parsed at all. Nothing from the failed attempt
                    # reached the history, so the same request can simply be sent again.
                    restarts += 1
                    if restarts > MAX_STREAM_RESTARTS:
                        raise
                    log.warning("unparseable streamed tool input; retrying (%d)", restarts)
                    yield {"type": "retry"}
        except MissingKeyError:
            yield {"type": "error", "code": "no_key", "message": "还没有设置 API key。去「设置」里填一下就能用了。"}
            return
        except anthropic.AuthenticationError:
            yield {"type": "error", "code": "auth", "message": "API key 无效或已被撤销。去「设置」里换一个。"}
            return
        except anthropic.PermissionDeniedError as e:
            yield {"type": "error", "code": "permission", "message": f"这个 API key 没有权限：{e.message}"}
            return
        except anthropic.NotFoundError:
            yield {"type": "error", "code": "model", "message": "找不到这个模型。去「设置」里换一个模型试试。"}
            return
        except anthropic.RateLimitError:
            yield {"type": "error", "code": "rate_limit", "message": "请求太频繁，或者账户余额/额度用完了。稍等一下再试，或去 Anthropic 控制台看看额度。"}
            return
        except anthropic.BadRequestError as e:
            yield {"type": "error", "code": "bad_request", "message": f"请求被拒绝：{e.message}"}
            return
        except anthropic.APIStatusError as e:
            yield {"type": "error", "code": "server", "message": f"Claude 服务暂时出错（{e.status_code}）。稍后再试。"}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "code": "network", "message": "连不上 Claude。检查一下网络再试。"}
            return
        except ValueError:
            yield {"type": "error", "code": "format", "message": "这次生成的内容格式有问题，再发一次试试。"}
            return
        finally:
            # A stop or crash between a tool call and its result would leave the history unusable.
            dangling = _open_tool_uses(history)
            if dangling:
                content = _cancel_results(dangling, "这次调用被中断了，没有执行。")
                self.store.append_message(conversation_id, "user", content)
                history.append({"role": "user", "content": content})

        yield {
            "type": "done",
            "cost": round(totals["cost"], 4),
            "context_tokens": totals["context"],
            "long_conversation": totals["context"] >= config.LONG_CONVERSATION_TOKENS,
        }

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
                    yield {"type": "tool_input", **tool, "input": _jsonable(event.snapshot)}
            elif kind == "content_block_stop":
                block = event.content_block
                if block.type == "tool_use":
                    yield {"type": "tool_input", "id": block.id, "name": block.name, "input": _jsonable(block.input), "final": True}
                    tool = None

    async def _after_message(
        self,
        conversation_id: int,
        history: list[dict[str, Any]],
        runner: Any,
        message: Any,
        totals: dict[str, Any],
    ) -> AsyncIterator[Event]:
        content = [serialize_block(b) for b in message.content]
        usage = message.usage
        cache_read = usage.cache_read_input_tokens or 0
        cache_write = usage.cache_creation_input_tokens or 0
        context = usage.input_tokens + cache_read + cache_write
        history.append({"role": "assistant", "content": content})
        self.store.append_message(conversation_id, "assistant", content, input_tokens=context)
        self.store.record_usage(
            conversation_id,
            message.model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read=cache_read,
            cache_write=cache_write,
        )
        totals["cost"] += config.estimate_cost(message.model, usage.input_tokens, usage.output_tokens, cache_read, cache_write)
        totals["context"] = context
        # Everything streamed so far is now part of the saved history; a later retry won't discard it.
        yield {"type": "message_done", "stop_reason": message.stop_reason}

        if any(b.get("type") == "fallback" for b in content):
            yield {"type": "notice", "level": "info", "text": f"这一轮由 {message.model} 接手回答。"}

        if message.stop_reason == "refusal":
            yield {"type": "notice", "level": "warn", "text": "Claude 没有回答这个请求。换个说法再试试。"}
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
        results = _jsonable(response["content"])
        history.append({"role": "user", "content": results})
        self.store.append_message(conversation_id, "user", results)

        names = {b["id"]: b["name"] for b in content if b.get("type") == "tool_use"}
        for result in results:
            tool_use_id = result.get("tool_use_id")
            name = names.get(tool_use_id, "")
            payload = _parse_result(result.get("content"))
            if name in SAVE_TOOLS and isinstance(payload, dict) and payload.get("card_id"):
                self.store.set_card_tool_use(int(payload["card_id"]), tool_use_id)
            yield {
                "type": "tool_result",
                "id": tool_use_id,
                "name": name,
                "is_error": bool(result.get("is_error")),
                "result": payload,
            }


def _parse_result(content: Any) -> Any:
    if isinstance(content, list):
        content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
    if isinstance(content, str):
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
    return content
