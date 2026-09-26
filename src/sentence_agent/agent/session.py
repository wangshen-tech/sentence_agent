"""One user turn, from the stored history through whichever provider is active, streamed to the UI.

The conversation history is mirrored into SQLite as it grows (append-only, full content blocks), so a
conversation resumes after a restart, can switch providers midway, and a failed or stopped turn never
leaves the history in a state the next request would be rejected for.
"""

from __future__ import annotations

import time
from typing import Any, AsyncIterator, Callable

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from .. import config
from ..providers import Provider, ProviderRegistry
from ..store import Store
from .anthropic_engine import AnthropicEngine
from .common import Event, Turn, cancel_results, open_tool_uses
from .openai_engine import OpenAIEngine
from .prompts import learner_profile
from .tools import ToolContext, build_tools

INTERRUPTED = "这次调用被中断了，没有执行。"

AnthropicFactory = Callable[[Provider, str], AsyncAnthropic]
OpenAIFactory = Callable[[Provider, str], AsyncOpenAI]


def _anthropic_client(provider: Provider, key: str) -> AsyncAnthropic:
    return AsyncAnthropic(api_key=key, base_url=provider.base_url or None, max_retries=2)


def _openai_client(provider: Provider, key: str) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=key, base_url=provider.base_url, max_retries=2, timeout=600)


class AgentService:
    def __init__(
        self,
        store: Store,
        providers: ProviderRegistry | None = None,
        anthropic_factory: AnthropicFactory = _anthropic_client,
        openai_factory: OpenAIFactory = _openai_client,
    ):
        self.store = store
        self.providers = providers or ProviderRegistry(store)
        self._anthropic_factory = anthropic_factory
        self._openai_factory = openai_factory
        self._clients: dict[tuple[str, str, str, str], Any] = {}

    def _client(self, provider: Provider, key: str) -> Any:
        cache_key = (provider.protocol, provider.base_url, key, provider.id)
        if cache_key not in self._clients:
            factory = self._anthropic_factory if provider.protocol == "anthropic" else self._openai_factory
            self._clients[cache_key] = factory(provider, key)
        return self._clients[cache_key]

    def effort(self) -> str:
        effort = self.store.get_setting("effort", config.DEFAULT_EFFORT) or config.DEFAULT_EFFORT
        return effort if effort in config.EFFORT_IDS else config.DEFAULT_EFFORT

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
        dangling = open_tool_uses(history)
        if dangling:
            # The app was closed or the turn was stopped between a tool call and its result.
            content = cancel_results(dangling, INTERRUPTED)
            self.store.append_message(conversation_id, "user", content)
            history.append({"role": "user", "content": content})
        return history

    # ---------- the turn ----------

    async def run_turn(self, conversation_id: int, text: str) -> AsyncIterator[Event]:
        conv = self.store.get_conversation(conversation_id)
        if conv is None:
            yield {"type": "error", "code": "not_found", "message": "找不到这段对话"}
            return

        provider = self.providers.active()
        key = self.providers.key(provider.id)
        if not key or not provider.model:
            yield {"type": "error", "code": "no_key", "message": f"「{provider.name}」还没有设置 API key 或模型。去「设置」里填一下就能用了。"}
            return

        history = self._load_history(conversation_id)
        user_content = [{"type": "text", "text": text}]
        self.store.append_message(conversation_id, "user", user_content)
        history.append({"role": "user", "content": user_content})
        if not conv["title"]:
            self.store.set_conversation_title(conversation_id, text.strip().splitlines()[0][:40])

        turn = Turn(self.store, conversation_id, history)
        client = self._client(provider, key)
        if provider.protocol == "anthropic":
            engine: Any = AnthropicEngine(client, provider, self.effort())
            eager = engine.full_features
        else:
            engine = OpenAIEngine(client, provider)
            eager = False
        tools = build_tools(ToolContext(self.store, conversation_id), eager_input_streaming=eager)

        failed = False
        yield {"type": "turn_start", "model": provider.model, "provider": provider.name}
        try:
            async for event in engine.run(turn, tools, conv["profile"]):
                failed = failed or event["type"] == "error"
                yield event
        finally:
            turn.close_dangling(INTERRUPTED)

        if not failed:
            yield {
                "type": "done",
                "cost": round(turn.cost, 4),
                "context_tokens": turn.context_tokens,
                "long_conversation": turn.context_tokens >= config.LONG_CONVERSATION_TOKENS,
            }
