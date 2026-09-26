"""Development server: the real app wired to a scripted fake Claude, with demo data in a throwaway folder.

    .venv/bin/python scripts/dev_server.py [--port 8765] [--home /tmp/sentence-agent-dev]

Open http://127.0.0.1:8765/#t=dev. Nothing here touches your real notebook or needs an API key.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

import httpx2  # noqa: E402
import uvicorn  # noqa: E402
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient  # noqa: E402

from tests.fake_api import _sse, finish, message_start, text_block, thinking_block, tool_block  # noqa: E402

CJK = re.compile(r"[㐀-鿿]")


def scripted_reply(body: dict) -> list[dict]:
    last = body["messages"][-1]
    if any(b.get("type") == "tool_result" for b in last["content"]):
        return [message_start(3000), *text_block(0, "存好了。", "**sleep on it** 这个说法很常用，", "下次想拖一拖再答复时可以直接用。"), *finish("end_turn")]
    text = " ".join(b.get("text", "") for b in last["content"] if b.get("type") == "text")
    if CJK.search(text) and not re.search(r"[A-Za-z]{3,}", text):
        tool = ("save_translation", {
            "zh": text.strip(),
            "best": "Let me sleep on it.",
            "versions": [
                {"en": "Let me sleep on it.", "tone": "口语", "note": "想一晚再答复，朋友之间很常用。"},
                {"en": "Let me think it over.", "tone": "日常", "note": "最通用，什么场合都行。"},
                {"en": "I'd like some time to consider it.", "tone": "正式", "note": "工作场合更礼貌。"},
            ],
            "phrases": [{"phrase": "sleep on it", "meaning": "睡一觉再决定", "example": "No rush. Sleep on it and let me know.", "example_zh": "不急，想一晚再告诉我。"}],
            "avoid": [{"en": "I need to consider consider.", "why": "中文叠词不能照搬。"}],
        })
    else:
        tool = ("save_analysis", {
            "en": text.strip(),
            "zh": "我现在不太想聊这个。",
            "parts": [
                {"text": "I", "role": "主语", "note": ""},
                {"text": "'d rather not get into", "role": "谓语", "note": "would rather not + 动词原形：宁愿不……"},
                {"text": "it", "role": "宾语", "note": ""},
                {"text": "right now.", "role": "状语", "note": "right now 强调“此刻”"},
            ],
            "skeleton": "I'd rather not get into it.",
            "pattern": "主谓宾",
            "explanation": "礼貌地回避一个话题。语气比 I don't want to talk about it 柔和。",
            "points": [{"title": "get into (a topic)", "explain": "深入谈论某个话题", "example": "Let's not get into politics.", "example_zh": "咱们别聊政治了。"}],
        })
    return [message_start(1800), *thinking_block(0, "先判断用户想做什么，再给出地道的说法。"), *tool_block(1, "toolu_" + os.urandom(4).hex(), tool[0], tool[1], pieces=12), *finish("tool_use")]


def fake_client() -> AsyncAnthropic:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(scripted_reply(body)))

    return AsyncAnthropic(api_key="sk-ant-dev", http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)))


def seed(store) -> None:
    if store.count_cards():
        return
    rows = [
        ("zh2en", "别往心里去。", "Don't take it personally.", "别往心里去。"),
        ("zh2en", "他叫什么来着，就在嘴边了。", "His name is on the tip of my tongue.", "他叫什么来着，就在嘴边了。"),
        ("en2zh", "我现在不太想聊这个。", "I'd rather not get into it right now.", "I'd rather not get into it right now."),
        ("check", "我非常喜欢这部电影，它让我哭了。", "I loved this movie. It made me cry.", "I very like this movie, it make me cry."),
        ("zh2en", "你说得有道理。", "You've got a point.", "你说得有道理。"),
    ]
    for kind, zh, en, source in rows:
        detail = {"best": en, "versions": [{"en": en, "tone": "日常", "note": ""}]} if kind == "zh2en" else {}
        if kind == "check":
            detail = {"original": source, "verdict": "wrong", "corrected": "I really like this movie. It made me cry.", "zh": zh,
                      "issues": [{"wrong": "very like", "fix": "really like", "type": "搭配", "why": "very 不能直接修饰动词。"},
                                 {"wrong": "make", "fix": "made", "type": "语法", "why": "说的是过去的事，用过去式。"}],
                      "natural": [{"en": en, "note": "口语里更自然"}], "comment": "意思表达清楚了，注意动词时态。"}
        if kind == "en2zh":
            detail = {"zh": zh, "parts": [{"text": "I", "role": "主语"}, {"text": "'d rather not get into", "role": "谓语"}, {"text": "it", "role": "宾语"}, {"text": "right now.", "role": "状语"}],
                      "skeleton": "I'd rather not get into it.", "pattern": "主谓宾", "explanation": "礼貌地回避话题。", "points": []}
        store.add_card(kind, zh, en, source=source, detail=detail)
    store.upsert_note("very + 动词", "very 不能直接修饰动词，用 really like / love。", "I very like this movie")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--home", default=str(Path(tempfile.gettempdir()) / "sentence-agent-dev"))
    args = parser.parse_args()
    os.environ["SENTENCE_AGENT_HOME"] = args.home

    from sentence_agent import config
    from sentence_agent.agent.session import AgentService
    from sentence_agent.server import create_app
    from sentence_agent.store import Store

    store = Store(config.db_path())
    seed(store)
    agent = AgentService(store, client_factory=fake_client)
    app = create_app(store, agent, token="dev")

    # The fake client needs no key; report one as present so the UI unlocks.
    import sentence_agent.credentials as creds
    creds.get_api_key = lambda: "sk-ant-dev-fake-key"
    creds.key_source = lambda: "env"

    print(f"dev server: http://127.0.0.1:{args.port}/#t=dev  (data: {args.home})", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
