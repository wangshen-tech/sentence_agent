"""End-to-end agent turns against the scripted API: real SDK tool runner, real tools, real SQLite."""

from __future__ import annotations

import json

import httpx2
import pytest

from sentence_agent.agent.session import AgentService
from sentence_agent.agent.transcript import build_transcript
from sentence_agent.store import Store

from .fake_api import FakeAPI, finish, message_start, text_block, thinking_block, tool_block

TRANSLATION = {
    "zh": "这事儿我得再考虑考虑。",
    "best": "Let me sleep on it.",
    "versions": [
        {"en": "Let me sleep on it.", "tone": "口语", "note": "想一晚再答复"},
        {"en": "Let me think it over.", "tone": "日常", "note": "最通用"},
    ],
    "phrases": [{"phrase": "sleep on it", "meaning": "睡一觉再决定", "example": "Sleep on it.", "example_zh": "想一晚。"}],
}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    yield s
    s.close()


async def collect(agent: AgentService, conversation_id: int, text: str) -> list[dict]:
    return [e async for e in agent.run_turn(conversation_id, text)]


async def test_translation_turn_saves_card_and_history(store):
    api = FakeAPI(
        [
            [message_start(), *thinking_block(0, "用户想要地道说法"), *tool_block(1, "toolu_1", "save_translation", TRANSLATION), *finish("tool_use")],
            [message_start(2400), *text_block(0, "记住 ", "**sleep on it** 就好。"), *finish("end_turn")],
        ]
    )
    agent = AgentService(store, client_factory=api.client)
    cid = agent.new_conversation()

    events = await collect(agent, cid, "这事儿我得再考虑考虑怎么说")
    kinds = [e["type"] for e in events]

    assert kinds[0] == "turn_start"
    assert "thinking_delta" in kinds and "tool_start" in kinds and "text_delta" in kinds
    assert kinds[-1] == "done"
    final_input = [e for e in events if e["type"] == "tool_input" and e.get("final")][0]
    assert final_input["input"]["best"] == "Let me sleep on it."
    result = [e for e in events if e["type"] == "tool_result"][0]
    assert result["is_error"] is False and result["result"]["card_id"]

    # The card is in the notebook, Chinese on one side and the recommended English on the other.
    card = store.get_card(result["result"]["card_id"])
    assert (card["kind"], card["zh"], card["en"]) == ("zh2en", "这事儿我得再考虑考虑。", "Let me sleep on it.")
    assert card["tool_use_id"] == "toolu_1" and card["due"]

    # History: user, assistant(thinking + tool_use), user(tool_result), assistant(text).
    history = store.load_history(cid)
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    assert history[1]["content"][0] == {"type": "thinking", "thinking": "用户想要地道说法", "signature": "sig-abc"}
    assert history[2]["content"][0]["tool_use_id"] == "toolu_1"

    # The second request replayed the full history, including the thinking signature and the tool result.
    second = api.requests[1]
    assert second["messages"][1]["content"][0]["signature"] == "sig-abc"
    assert second["messages"][2]["content"][0]["type"] == "tool_result"

    # Transcript for the UI merges the turn and links the card.
    items = build_transcript(store, cid)
    assert [i["role"] for i in items] == ["user", "assistant"]
    blocks = items[1]["blocks"]
    assert [b["type"] for b in blocks] == ["thinking", "tool", "text"]
    assert blocks[1]["card"]["id"] == card["id"]

    # Usage was recorded for the cost estimate.
    assert store.usage_by_model(0)[0]["requests"] == 2


async def test_request_shape(store):
    api = FakeAPI([[message_start(), *text_block(0, "Hi"), *finish("end_turn")]])
    agent = AgentService(store, client_factory=api.client)
    cid = agent.new_conversation()
    await collect(agent, cid, "hello")

    body, headers = api.requests[0], api.headers[0]
    assert body["model"] == "claude-opus-5"
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "medium"}
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert body["stream"] is True
    names = {t["name"] for t in body["tools"]}
    assert names == {"save_translation", "save_analysis", "save_correction", "search_notebook", "remember_weak_point", "get_study_overview"}
    assert all(t.get("eager_input_streaming") is True for t in body["tools"])
    assert "$ref" not in json.dumps(body["tools"])


async def test_sonnet_has_no_server_fallbacks(store):
    store.set_setting("model", "claude-sonnet-5")
    api = FakeAPI([[message_start(), *text_block(0, "Hi"), *finish("end_turn")]])
    agent = AgentService(store, client_factory=api.client)
    await collect(agent, agent.new_conversation(), "hello")
    assert "fallbacks" not in api.requests[0]
    assert "anthropic-beta" not in api.headers[0] or "server-side-fallback" not in api.headers[0]["anthropic-beta"]


async def test_invalid_tool_input_is_reported_back_to_claude(store):
    bad = {"zh": "你好", "best": "Hello"}  # versions missing
    api = FakeAPI(
        [
            [message_start(), *tool_block(0, "toolu_bad", "save_translation", bad), *finish("tool_use")],
            [message_start(), *text_block(0, "OK"), *finish("end_turn")],
        ]
    )
    agent = AgentService(store, client_factory=api.client)
    cid = agent.new_conversation()
    events = await collect(agent, cid, "你好")
    result = [e for e in events if e["type"] == "tool_result"][0]
    assert result["is_error"] is True
    assert store.count_cards() == 0
    assert api.requests[1]["messages"][-1]["content"][0]["is_error"] is True


async def test_auth_error_is_a_friendly_event(store):
    api = FakeAPI([httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})])
    agent = AgentService(store, client_factory=api.client)
    events = await collect(agent, agent.new_conversation(), "hi")
    assert events[-1]["type"] == "error" and events[-1]["code"] == "auth"


async def test_interrupted_tool_call_is_repaired_before_next_turn(store):
    agent = AgentService(store, client_factory=lambda: None)  # never called
    cid = agent.new_conversation()
    store.append_message(cid, "user", [{"type": "text", "text": "你好"}])
    store.append_message(cid, "assistant", [{"type": "tool_use", "id": "toolu_x", "name": "search_notebook", "input": {"query": "a"}}])
    history = agent._load_history(cid)
    assert history[-1]["role"] == "user"
    assert history[-1]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "toolu_x",
        "content": "这次调用被中断了，没有执行。",
        "is_error": True,
    }


async def test_refusal_stops_without_running_tools(store):
    api = FakeAPI(
        [[message_start(), *tool_block(0, "toolu_r", "save_translation", TRANSLATION), *finish("refusal")]]
    )
    agent = AgentService(store, client_factory=api.client)
    cid = agent.new_conversation()
    events = await collect(agent, cid, "x")
    assert any(e["type"] == "notice" for e in events)
    assert store.count_cards() == 0
    # The cut-off tool call got a closing result so the next turn is still a valid request.
    assert store.load_history(cid)[-1]["content"][0]["tool_use_id"] == "toolu_r"
