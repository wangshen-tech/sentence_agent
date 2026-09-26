from __future__ import annotations

import time

import pytest

from sentence_agent import srs
from sentence_agent.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def test_cards_filters_and_search(store):
    a = store.add_card("zh2en", "别往心里去", "Don't take it personally.", source="别往心里去")
    b = store.add_card("en2zh", "我现在不想聊这个", "I'd rather not get into it.", source="I'd rather not get into it.")
    assert store.count_cards() == 2
    assert [c["id"] for c in store.search_cards("personally")] == [a]
    assert [c["id"] for c in store.search_cards("不想 聊")] == [b]
    assert store.count_cards(filter="due") == 2  # new cards are due right away
    store.record_review(a, remembered=True)
    assert store.count_cards(filter="due") == 1
    store.record_review(b, remembered=False)
    assert [c["id"] for c in store.list_cards(filter="weak")] == [b]


def test_review_schedule():
    now = time.time()
    level, due = srs.schedule(0, True, now)
    assert level == 1 and due > now
    level, due = srs.schedule(5, True, now)
    assert level == 5
    level, due = srs.schedule(3, False, now)
    assert level == 0 and due == now


def test_update_and_delete_card(store):
    cid = store.add_card("check", "我很喜欢这部电影", "I really like this movie.")
    updated = store.update_card(cid, en="I love this movie.")
    assert updated["en"] == "I love this movie."
    assert store.delete_card(cid)
    assert store.get_card(cid) is None


def test_learner_notes_accumulate(store):
    store.upsert_note("冠词 a/the", "可数名词单数前要有冠词", "I have car")
    row = store.upsert_note("冠词 A/THE", "可数名词单数前要有冠词")
    assert row["count"] == 2 and row["example"] == "I have car"
    assert store.top_notes()[0]["topic"] == "冠词 a/the"


def test_conversation_history_roundtrip(store):
    cid = store.create_conversation(profile="p")
    store.append_message(cid, "user", [{"type": "text", "text": "你好"}])
    store.append_message(cid, "assistant", [{"type": "text", "text": "Hi"}], input_tokens=500)
    assert store.load_history(cid) == [
        {"role": "user", "content": [{"type": "text", "text": "你好"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Hi"}]},
    ]
    assert store.last_input_tokens(cid) == 500
    store.delete_conversation(cid)
    assert store.load_history(cid) == []
