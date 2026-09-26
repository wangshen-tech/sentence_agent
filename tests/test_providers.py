"""Other providers: OpenAI-compatible endpoints, Anthropic-format relays, and switching between them."""

from __future__ import annotations

import json

import httpx2
import pytest

from sentence_agent.agent.history import for_anthropic, portable_schema, to_openai
from sentence_agent.agent.session import AgentService
from sentence_agent.agent.transcript import build_transcript
from sentence_agent.providers import ProviderRegistry, normalize_base_url
from sentence_agent.store import Store

from .fake_api import (
    FakeAPI,
    FakeOpenAI,
    chunk,
    finish,
    message_start,
    openai_text,
    openai_tool_call,
    text_block,
)
from .test_agent_loop import TRANSLATION


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "p.db")
    yield s
    s.close()


def registry(store: Store) -> ProviderRegistry:
    return ProviderRegistry(store, get_key=lambda pid: "sk-test", key_source=lambda pid: "keychain")


async def collect(agent: AgentService, cid: int, text: str) -> list[dict]:
    return [e async for e in agent.run_turn(cid, text)]


# ---------- provider registry ----------


def test_default_provider_carries_over_the_old_model_setting(store):
    store.set_setting("model", "claude-sonnet-5")
    reg = registry(store)
    [only] = reg.all()
    assert (only.id, only.protocol, only.model, only.official_anthropic) == ("anthropic", "anthropic", "claude-sonnet-5", True)
    assert reg.active().id == "anthropic"


def test_create_activate_delete(store):
    reg = registry(store)
    ds = reg.create("DeepSeek", "openai", " https://api.deepseek.com/ ", "deepseek-chat")
    assert ds.base_url == "https://api.deepseek.com"
    reg.activate(ds.id)
    assert reg.active().id == ds.id
    reg.delete(ds.id)
    assert reg.active().id == "anthropic"
    with pytest.raises(ValueError):
        reg.delete("anthropic")  # the last one stays


def test_validation_and_url_normalization(store):
    reg = registry(store)
    with pytest.raises(ValueError):
        reg.create("x", "openai", "", "m")  # OpenAI format needs a URL
    with pytest.raises(ValueError):
        reg.create("x", "openai", "ftp://relay", "m")
    relay = reg.create("Relay", "anthropic", "https://relay.example.com/v1/", "claude-x")
    assert relay.base_url == "https://relay.example.com" and not relay.official_anthropic
    assert normalize_base_url("openai", "https://relay.example.com/v1/") == "https://relay.example.com/v1"


# ---------- history conversion ----------


def test_portable_schema_removes_what_strict_providers_reject():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "title": "Args",
        "properties": {
            "tags": {"anyOf": [{"type": "array", "items": {"type": "string", "title": "T"}}, {"type": "null"}], "default": None, "description": "d"},
        },
        "required": [],
    }
    assert portable_schema(schema) == {
        "type": "object",
        "properties": {"tags": {"type": "array", "items": {"type": "string"}, "description": "d"}},
        "required": [],
    }


def test_history_conversions():
    history = [
        {"role": "user", "content": [{"type": "text", "text": "你好"}]},
        {"role": "assistant", "content": [
            {"type": "reasoning", "text": "想一想"},
            {"type": "thinking", "thinking": "t", "signature": "s"},
            {"type": "text", "text": "OK"},
            {"type": "tool_use", "id": "call_1", "name": "search_notebook", "input": {"query": "a"}},
        ]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": '{"count": 0}'}]},
    ]
    oa = to_openai(history, "SYS")
    assert oa[0] == {"role": "system", "content": "SYS"}
    assert oa[2]["tool_calls"][0]["function"] == {"name": "search_notebook", "arguments": '{"query": "a"}'}
    assert oa[2]["content"] == "OK"
    assert oa[3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"count": 0}'}
    official = for_anthropic(history, official=True)
    assert [b["type"] for b in official[1]["content"]] == ["thinking", "text", "tool_use"]
    relay = for_anthropic(history, official=False)
    assert [b["type"] for b in relay[1]["content"]] == ["text", "tool_use"]


# ---------- OpenAI-compatible engine ----------


def openai_agent(store: Store, fake: FakeOpenAI, anthropic_fake: FakeAPI | None = None) -> AgentService:
    reg = registry(store)
    provider = reg.create("DeepSeek", "openai", "https://relay.test/v1", "deepseek-chat")
    reg.activate(provider.id)
    return AgentService(
        store,
        providers=reg,
        openai_factory=lambda p, k: fake.client(p.base_url),
        anthropic_factory=lambda p, k: anthropic_fake.client(p.base_url or None) if anthropic_fake else None,
    )


async def test_openai_compatible_turn_saves_card(store):
    fake = FakeOpenAI(
        [
            [
                chunk({"role": "assistant", "content": None, "reasoning_content": "用户想要地道说法"}),
                *openai_tool_call("call_1", "save_translation", TRANSLATION),
                chunk({}, finish="tool_calls"),
                chunk(None, usage={"prompt_tokens": 900, "completion_tokens": 120, "total_tokens": 1020}),
            ],
            [*openai_text("记住 ", "**sleep on it**。"), chunk({}, finish="stop")],
        ]
    )
    agent = openai_agent(store, fake)
    cid = agent.new_conversation()
    events = await collect(agent, cid, "这事儿我得再考虑考虑")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "turn_start" and kinds[-1] == "done"
    assert "thinking_delta" in kinds and "tool_start" in kinds and "text_delta" in kinds
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["is_error"] is False
    card = store.get_card(result["result"]["card_id"])
    assert (card["zh"], card["en"], card["tool_use_id"]) == ("这事儿我得再考虑考虑。", "Let me sleep on it.", "call_1")

    # Stored in the common format; the reasoning is kept for display but never sent back.
    history = store.load_history(cid)
    assert [b["type"] for b in history[1]["content"]] == ["reasoning", "tool_use"]
    second = fake.requests[1]["messages"]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert "reasoning" not in json.dumps(second, ensure_ascii=False)
    assert json.loads(second[2]["tool_calls"][0]["function"]["arguments"])["best"] == "Let me sleep on it."

    # Request shape: portable function schemas, streamed usage, the configured endpoint.
    first = fake.requests[0]
    assert first["model"] == "deepseek-chat" and first["stream"] is True
    assert first["stream_options"] == {"include_usage": True}
    params = next(t for t in first["tools"] if t["function"]["name"] == "save_translation")["function"]["parameters"]
    assert "anyOf" not in json.dumps(params) and "title" not in json.dumps(params)
    assert fake.urls[0] == "https://relay.test/v1/chat/completions"
    assert store.usage_by_model(0)[0]["input_tokens"] == 900

    items = build_transcript(store, cid)
    assert [b["type"] for b in items[1]["blocks"]] == ["thinking", "tool", "text"]
    assert items[1]["blocks"][1]["card"]["id"] == card["id"]


async def test_stream_options_rejected_is_retried_without_it(store):
    rejected = httpx2.Response(400, json={"error": {"message": "unknown field stream_options"}})
    fake = FakeOpenAI([rejected, [*openai_text("Hi"), chunk({}, finish="stop")]])
    agent = openai_agent(store, fake)
    events = await collect(agent, agent.new_conversation(), "hello")
    assert events[-1]["type"] == "done"
    assert "stream_options" not in fake.requests[1]


async def test_invalid_tool_arguments_go_back_as_an_error(store):
    bad_call = [
        chunk({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_x", "type": "function", "function": {"name": "save_translation", "arguments": '{"zh": "你好", "best": '}}]}),
        chunk({}, finish="tool_calls"),
    ]
    fake = FakeOpenAI([bad_call, [*openai_text("抱歉"), chunk({}, finish="stop")]])
    agent = openai_agent(store, fake)
    cid = agent.new_conversation()
    events = await collect(agent, cid, "你好")
    assert next(e for e in events if e["type"] == "tool_result")["is_error"] is True
    assert store.count_cards() == 0
    tool_msg = fake.requests[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and "INVALID_JSON" in tool_msg["content"]
    # Nothing app-internal leaked into the stored history.
    assert all(set(b) <= {"type", "id", "name", "input", "text"} for b in store.load_history(cid)[1]["content"])


async def test_openai_auth_error(store):
    fake = FakeOpenAI([httpx2.Response(401, json={"error": {"message": "Incorrect API key"}})])
    agent = openai_agent(store, fake)
    events = await collect(agent, agent.new_conversation(), "hi")
    assert events[-1]["type"] == "error" and events[-1]["code"] == "auth"


# ---------- Anthropic-format relays and switching ----------


async def test_anthropic_relay_gets_the_plain_request_shape(store):
    api = FakeAPI([[message_start(), *text_block(0, "Hi"), *finish("end_turn")]])
    reg = registry(store)
    relay = reg.create("Relay", "anthropic", "https://relay.example.com/v1", "claude-sonnet-4-5")
    reg.activate(relay.id)
    agent = AgentService(store, providers=reg, anthropic_factory=lambda p, k: api.client(p.base_url))
    events = await collect(agent, agent.new_conversation(), "hello")
    assert events[-1]["type"] == "done"
    body = api.requests[0]
    assert api.urls[0].startswith("https://relay.example.com/v1/messages")
    for field in ("thinking", "output_config", "cache_control", "fallbacks"):
        assert field not in body
    assert isinstance(body["system"], str)
    assert all("eager_input_streaming" not in t for t in body["tools"])
    assert "anthropic-beta" not in api.headers[0]


async def test_switching_from_openai_to_claude_mid_conversation(store):
    fake = FakeOpenAI(
        [[chunk({"role": "assistant", "reasoning_content": "嗯"}), *openai_text("Hi there"), chunk({}, finish="stop")]]
    )
    api = FakeAPI([[message_start(), *text_block(0, "Welcome back"), *finish("end_turn")]])
    agent = openai_agent(store, fake, anthropic_fake=api)
    cid = agent.new_conversation()
    await collect(agent, cid, "hello")

    agent.providers.activate("anthropic")
    events = await collect(agent, cid, "again")
    assert events[-1]["type"] == "done"
    sent = api.requests[0]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[1]["content"] == [{"type": "text", "text": "Hi there"}]  # the reasoning block stayed home
