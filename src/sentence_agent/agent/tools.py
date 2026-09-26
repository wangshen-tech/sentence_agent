"""Tools the agent can call.

The three save_* tools are how every sentence gets into the notebook. Their inputs are also what the
UI renders as a card, streamed in as Claude writes them. The other tools let the agent look back at
the notebook and keep a long-term record of the learner's weak points.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from anthropic import beta_async_tool
from pydantic import BaseModel, Field

from ..store import Store

SAVE_TOOLS = ("save_translation", "save_analysis", "save_correction")


# ---------- card shapes ----------


class Version(BaseModel):
    en: str = Field(description="英文说法")
    tone: str = Field(description="两个字概括语气，比如 口语、日常、正式、书面、委婉")
    note: str = Field("", description="一句中文：什么场合用，和其他说法有什么不同")


class Phrase(BaseModel):
    phrase: str = Field(description="值得记住的地道表达：短语、搭配或句型")
    meaning: str = Field(description="中文解释")
    example: str = Field("", description="一个新的英文例句")
    example_zh: str = Field("", description="例句的中文翻译")


class Pitfall(BaseModel):
    en: str = Field(description="中国学生常见、但母语者不会这么说的英文")
    why: str = Field(description="为什么不自然")


class Part(BaseModel):
    text: str = Field(description="按顺序从原句切出的一块；所有块按顺序拼起来等于原句，标点放进相邻的块")
    role: str = Field(
        description="句子成分：主语、谓语、宾语、间接宾语、表语、宾语补足语、定语、状语、同位语、插入语、连词、"
        "主语从句、宾语从句、表语从句、定语从句、状语从句、非谓语 等"
    )
    note: str = Field("", description="这一块内部结构或修饰对象的一句中文说明；不需要就留空")


class Point(BaseModel):
    title: str = Field(description="表达或语法点本身")
    explain: str = Field(description="中文解释")
    example: str = Field("", description="英文例句")
    example_zh: str = Field("", description="例句的中文翻译")


class Issue(BaseModel):
    wrong: str = Field(description="原句里有问题的片段")
    fix: str = Field(description="改成什么")
    type: str = Field(description="语法、用词、搭配、语气、拼写、标点 之一")
    why: str = Field(description="中文解释原因")


class Alternative(BaseModel):
    en: str = Field(description="母语者更常说的版本")
    note: str = Field("", description="语气或场合的中文说明")


# ---------- helpers ----------


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Pydantic emits nested models as $defs + $ref. Inline them so the tool schema is self-contained."""
    defs = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                target = defs[node["$ref"].split("/")[-1]]
                merged = {**resolve(target), **{k: v for k, v in node.items() if k != "$ref"}}
                return merged
            return {k: resolve(v) for k, v in node.items() if k != "$defs"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def _dump(items: list[BaseModel] | None) -> list[dict[str, Any]]:
    return [i.model_dump() for i in items or []]


def _ok(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


@dataclass
class ToolContext:
    store: Store
    conversation_id: int
    saved_card_ids: list[int] = field(default_factory=list)


def build_tools(ctx: ToolContext) -> list[Any]:
    store = ctx.store

    def _save(kind: str, zh: str, en: str, source: str, detail: dict[str, Any]) -> str:
        card_id = store.add_card(kind, zh, en, source=source, detail=detail, conversation_id=ctx.conversation_id)
        ctx.saved_card_ids.append(card_id)
        return _ok(card_id=card_id, notebook_total=store.count_cards())

    @beta_async_tool(eager_input_streaming=True)
    async def save_translation(
        zh: str,
        best: str,
        versions: list[Version],
        phrases: list[Phrase] | None = None,
        avoid: list[Pitfall] | None = None,
    ) -> str:
        """把一句中文的地道英文说法存进用户的句子本，同时作为卡片展示给用户。

        用户给出一句中文、想知道英文怎么说时调用；一次给了几句就逐句各调用一次。

        Args:
            zh: 要翻译的那句中文本身。去掉“怎么说”“用英文怎么表达”之类的提问；有明显错别字就改正。
            best: 最推荐、最常用的一句英文，内容必须和 versions 中的某一句完全相同。
            versions: 2 到 4 个语气不同的英文说法。
            phrases: 1 到 4 个值得记住的地道表达。
            avoid: 中国学生常见的直译或错误说法；没有就不填。
        """
        detail = {"best": best, "versions": _dump(versions), "phrases": _dump(phrases), "avoid": _dump(avoid)}
        return _save("zh2en", zh, best, zh, detail)

    @beta_async_tool(eager_input_streaming=True)
    async def save_analysis(
        en: str,
        zh: str,
        parts: list[Part],
        skeleton: str = "",
        pattern: str = "",
        explanation: str = "",
        points: list[Point] | None = None,
    ) -> str:
        """把一句英文的中文意思和句子结构解析存进用户的句子本，同时作为卡片展示给用户。

        用户给出一句英文、想弄懂它的意思或结构时调用；一次给了几句就逐句各调用一次。

        Args:
            en: 要解析的英文原句本身。去掉用户另外加的提问，原句的用词不要改。
            zh: 自然的中文翻译，不要翻译腔。
            parts: 把原句按顺序切成 3 到 8 块并标注句子成分。
            skeleton: 去掉修饰成分后的句子主干（英文）。
            pattern: 句型，比如“主谓宾”“主系表”“there be 句型”。
            explanation: 用 2 到 4 句中文讲清这句话怎么理解：语气、言外之意、使用场景。
            points: 1 到 4 个值得学的表达或语法点。
        """
        detail = {
            "zh": zh,
            "parts": _dump(parts),
            "skeleton": skeleton,
            "pattern": pattern,
            "explanation": explanation,
            "points": _dump(points),
        }
        return _save("en2zh", zh, en, en, detail)

    @beta_async_tool(eager_input_streaming=True)
    async def save_correction(
        original: str,
        verdict: str,
        corrected: str,
        zh: str,
        issues: list[Issue] | None = None,
        natural: list[Alternative] | None = None,
        comment: str = "",
    ) -> str:
        """检查用户自己写的英文，把结果存进句子本，同时作为卡片展示给用户。

        用户给出自己写的或说过的英文，想知道语法对不对、地道不地道时调用；一次给了几句就逐句各调用一次。
        句子本里记下的是地道的版本，方便用户以后复习。

        Args:
            original: 用户写的英文原句本身，一个字都不要改。
            verdict: natural 表示语法正确而且地道；ok 表示基本没错、能听懂，但母语者一般不这么说；wrong 表示有语法、用词或拼写错误。
            corrected: 尽量保留原意和原句结构、只改必要地方的版本；已经没问题就和原句相同。
            zh: 这句话想表达的中文意思。
            issues: 每个问题一条；没有问题就不填。
            natural: 1 到 3 个母语者更常说的版本。
            comment: 一两句中文总评：先说做得好的地方，再说最需要注意的一点。
        """
        verdict = verdict if verdict in ("natural", "ok", "wrong") else "ok"
        alternatives = _dump(natural)
        best = corrected if verdict == "natural" or not alternatives else alternatives[0]["en"]
        detail = {
            "original": original,
            "verdict": verdict,
            "corrected": corrected,
            "zh": zh,
            "issues": _dump(issues),
            "natural": alternatives,
            "comment": comment,
        }
        return _save("check", zh, best or corrected or original, original, detail)

    @beta_async_tool(eager_input_streaming=True)
    async def search_notebook(query: str, limit: int = 5) -> str:
        """在用户的句子本里查以前存过的句子。

        用户问起以前查过的句子（“我之前问过的那句……怎么说来着”），或者你想看看用户是否查过相关表达时调用。

        Args:
            query: 关键词，中文或英文都可以，多个关键词用空格分开。
            limit: 最多返回几条，默认 5。
        """
        cards = store.search_cards(query, limit=max(1, min(limit, 20)))
        rows = [
            {"id": c["id"], "zh": c["zh"], "en": c["en"], "kind": c["kind"], "level": c["level"]} for c in cards
        ]
        return json.dumps({"results": rows, "count": len(rows)}, ensure_ascii=False)

    @beta_async_tool(eager_input_streaming=True)
    async def remember_weak_point(topic: str, note: str, example: str = "") -> str:
        """记下用户反复出现的易错点，存进长期记忆，以后的对话里也能看到。

        只在用户的英文里反复出现同一类问题、或者出现很典型的中式错误时调用。同一个易错点再次出现时用相同的 topic，次数会累加。

        Args:
            topic: 易错点的简短名称，比如“冠词 a/the”“一般过去时”“very + 动词”。
            note: 一句中文说明这个问题和正确用法。
            example: 用户原话里的一个例子（可选）。
        """
        row = store.upsert_note(topic, note, example)
        return _ok(topic=row["topic"], count=row["count"])

    @beta_async_tool(eager_input_streaming=True)
    async def get_study_overview() -> str:
        """查看用户的学习概况：句子本总数、今天该复习多少、记住了多少，以及记下的易错点。

        用户问起学习进度、今天该复习什么、自己常犯什么错时调用。
        """
        notes = [{"topic": n["topic"], "note": n["note"], "count": n["count"]} for n in store.top_notes(10)]
        return json.dumps({"stats": store.stats(), "weak_points": notes}, ensure_ascii=False)

    tools = [save_translation, save_analysis, save_correction, search_notebook, remember_weak_point, get_study_overview]
    for tool in tools:
        tool.input_schema = _inline_refs(tool.input_schema)
    return tools
