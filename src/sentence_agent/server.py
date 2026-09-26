"""Local HTTP API for the desktop window.

Bound to 127.0.0.1 only. Every /api request must carry the per-launch token in the X-App-Token header;
a custom header also means a web page in some other browser tab cannot call this API.
"""

from __future__ import annotations

import asyncio
import csv
import hmac
import json
import shutil
import subprocess
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import anthropic
import openai
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config, credentials, srs
from .agent.session import AgentService
from .agent.transcript import build_transcript
from .providers import list_models
from .store import CARD_FILTERS, REVIEW_SCOPES, Store

STATIC_DIR = Path(__file__).parent / "static"


class ChatIn(BaseModel):
    conversation_id: int
    text: str


class SettingsIn(BaseModel):
    effort: str | None = None


class ProviderIn(BaseModel):
    name: str
    protocol: str
    base_url: str = ""
    model: str
    api_key: str | None = None
    activate: bool = False


class ModelsIn(BaseModel):
    protocol: str
    base_url: str = ""
    api_key: str | None = None
    provider_id: str | None = None


class CardPatch(BaseModel):
    zh: str | None = None
    en: str | None = None


class ReviewIn(BaseModel):
    remembered: bool


class TextIn(BaseModel):
    text: str


_voice_args: list[str] | None = None


def _english_voice_args() -> list[str]:
    """Prefer an American English voice for `say`; fall back to the system default if none is installed."""
    global _voice_args
    if _voice_args is None:
        _voice_args = []
        try:
            voices = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=5).stdout
            for name in ("Samantha", "Ava", "Allison", "Alex"):
                if any(line.startswith(name + " ") for line in voices.splitlines()):
                    _voice_args = ["-v", name]
                    break
        except (OSError, subprocess.SubprocessError):
            pass
    return _voice_args


def create_app(store: Store, agent: AgentService, token: str, static_dir: Path = STATIC_DIR) -> FastAPI:
    app = FastAPI(title="SentenceAgent", docs_url=None, redoc_url=None, openapi_url=None)
    busy: set[int] = set()
    speaker: dict[str, subprocess.Popen | None] = {"proc": None}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            supplied = request.headers.get("x-app-token", "")
            if not hmac.compare_digest(supplied, token):
                return JSONResponse({"detail": "forbidden"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    # ---------- page ----------

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # ---------- app state & settings ----------

    def usage_summary() -> dict[str, Any]:
        now = time.time()
        today = srs.start_of_day(now)
        month = datetime.fromtimestamp(now).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()

        def total(since: float) -> dict[str, Any]:
            rows = store.usage_by_model(since)
            priced = [r for r in rows if r["model"] in config.MODELS_BY_ID]
            cost = sum(
                config.estimate_cost(r["model"], r["input_tokens"], r["output_tokens"], r["cache_read"], r["cache_write"])
                for r in priced
            )
            tokens = sum(r["input_tokens"] + r["output_tokens"] + r["cache_read"] + r["cache_write"] for r in rows)
            unpriced = sorted({r["model"] for r in rows} - {r["model"] for r in priced})
            return {"cost": round(cost, 4), "tokens": tokens, "unpriced_models": unpriced}

        return {"today": total(today), "month": total(month)}

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        providers = agent.providers.describe()
        active = agent.providers.active()
        active_info = next(p for p in providers if p["id"] == active.id)
        return {
            "version": __version__,
            "has_key": active_info["ready"],
            "providers": providers,
            "active_provider": active.id,
            "presets": [asdict(p) for p in config.PRESETS],
            "anthropic_models": [
                {"id": m.id, "label": m.label, "note": m.note, "input": m.input_per_mtok, "output": m.output_per_mtok}
                for m in config.MODELS
            ],
            "effort": agent.effort(),
            "efforts": [{"id": i, "label": label, "note": note} for i, label, note in config.EFFORTS],
            "data_dir": str(config.data_dir()),
            "stats": store.stats(),
            "usage": usage_summary(),
            "conversation_id": agent.current_conversation(),
        }

    @app.post("/api/settings")
    async def update_settings(body: SettingsIn) -> dict[str, Any]:
        if body.effort is not None:
            if body.effort not in config.EFFORT_IDS:
                raise HTTPException(400, "未知的思考深度")
            store.set_setting("effort", body.effort)
        return {"effort": agent.effort()}

    # ---------- providers ----------

    def _clean_key(raw: str | None) -> str | None:
        key = (raw or "").strip()
        if not key:
            return None
        if not key.isascii() or any(c.isspace() for c in key):
            raise HTTPException(400, "API key 里有空格、中文或其他特殊字符，检查一下是不是复制完整了。")
        return key

    def _store_key(provider_id: str, key: str | None) -> None:
        if key is None:
            return
        try:
            credentials.set_key(provider_id, key)
        except credentials.CredentialError as e:
            raise HTTPException(500, str(e))

    @app.post("/api/providers")
    async def create_provider(body: ProviderIn) -> dict[str, Any]:
        key = _clean_key(body.api_key)
        try:
            provider = agent.providers.create(body.name, body.protocol, body.base_url, body.model)
        except ValueError as e:
            raise HTTPException(400, str(e))
        _store_key(provider.id, key)
        if body.activate:
            agent.providers.activate(provider.id)
        return {"id": provider.id, "providers": agent.providers.describe()}

    @app.put("/api/providers/{provider_id}")
    async def update_provider(provider_id: str, body: ProviderIn) -> dict[str, Any]:
        key = _clean_key(body.api_key)
        try:
            agent.providers.update(provider_id, body.name, body.protocol, body.base_url, body.model)
        except KeyError:
            raise HTTPException(404, "找不到这个服务商")
        except ValueError as e:
            raise HTTPException(400, str(e))
        _store_key(provider_id, key)
        if body.activate:
            agent.providers.activate(provider_id)
        return {"id": provider_id, "providers": agent.providers.describe()}

    @app.delete("/api/providers/{provider_id}")
    async def delete_provider(provider_id: str) -> dict[str, Any]:
        try:
            agent.providers.delete(provider_id)
        except KeyError:
            raise HTTPException(404, "找不到这个服务商")
        except ValueError as e:
            raise HTTPException(400, str(e))
        try:
            credentials.delete_key(provider_id)
        except credentials.CredentialError:
            pass
        return {"providers": agent.providers.describe(), "active_provider": agent.providers.active().id}

    @app.delete("/api/providers/{provider_id}/key")
    async def delete_provider_key(provider_id: str) -> dict[str, Any]:
        try:
            credentials.delete_key(provider_id)
        except credentials.CredentialError as e:
            raise HTTPException(500, str(e))
        return {"providers": agent.providers.describe()}

    @app.post("/api/providers/{provider_id}/activate")
    async def activate_provider(provider_id: str) -> dict[str, Any]:
        try:
            provider = agent.providers.activate(provider_id)
        except KeyError:
            raise HTTPException(404, "找不到这个服务商")
        return {"active_provider": provider.id, "providers": agent.providers.describe()}

    @app.post("/api/providers/models")
    async def provider_models(body: ModelsIn) -> dict[str, Any]:
        """List the endpoint's models with the given (or stored) key. This is the connection test."""
        key = _clean_key(body.api_key) or (agent.providers.key(body.provider_id) if body.provider_id else None)
        if not key:
            raise HTTPException(400, "先填 API key")
        if body.protocol not in config.PROTOCOLS:
            raise HTTPException(400, "未知的接口格式")
        if body.protocol == "openai" and not body.base_url.strip():
            raise HTTPException(400, "先填接口地址")
        try:
            models = await list_models(body.protocol, body.base_url, key)
        except (anthropic.AuthenticationError, openai.AuthenticationError):
            raise HTTPException(400, "连上了，但 API key 无效。检查一下 key 是否正确、是否属于这个服务商。")
        except (anthropic.PermissionDeniedError, openai.PermissionDeniedError):
            raise HTTPException(400, "连上了，但这个 key 没有权限。")
        except (anthropic.NotFoundError, openai.NotFoundError):
            hint = "Anthropic 格式的地址一般不带 /v1。" if body.protocol == "anthropic" else "OpenAI 格式的地址一般要以 /v1 结尾。"
            raise HTTPException(400, f"这个地址没有提供模型列表。可能是地址不对（{hint}），也可能是服务商不支持查询模型，可以直接手填模型名。")
        except (anthropic.APIConnectionError, openai.APIConnectionError):
            raise HTTPException(400, "连不上这个地址。检查一下网络和接口地址。")
        except (anthropic.APIStatusError, openai.APIStatusError) as e:
            raise HTTPException(400, f"服务商返回了错误（{e.status_code}）：{e.message}")
        return {"models": models[:400], "count": len(models)}

    # ---------- conversations & chat ----------

    @app.get("/api/conversations")
    async def conversations() -> dict[str, Any]:
        return {"conversations": store.list_conversations()}

    @app.post("/api/conversations")
    async def new_conversation() -> dict[str, Any]:
        return {"id": agent.new_conversation()}

    @app.get("/api/conversations/{conversation_id}")
    async def conversation(conversation_id: int) -> dict[str, Any]:
        conv = store.get_conversation(conversation_id)
        if conv is None:
            raise HTTPException(404, "找不到这段对话")
        return {
            "conversation": conv,
            "items": build_transcript(store, conversation_id),
            "context_tokens": store.last_input_tokens(conversation_id),
            "busy": conversation_id in busy,
        }

    @app.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: int) -> dict[str, Any]:
        if conversation_id in busy:
            raise HTTPException(409, "这段对话还在回答中")
        store.delete_conversation(conversation_id)
        return {"ok": True}

    @app.post("/api/chat")
    async def chat(body: ChatIn) -> StreamingResponse:
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "消息是空的")
        if len(text) > 8000:
            raise HTTPException(400, "一次发的内容太长了，分几次发吧")
        if store.get_conversation(body.conversation_id) is None:
            raise HTTPException(404, "找不到这段对话")
        if body.conversation_id in busy:
            raise HTTPException(409, "上一条还在回答中")
        busy.add(body.conversation_id)

        async def events():
            try:
                async for event in agent.run_turn(body.conversation_id, text):
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - last line of defence, surfaced in the UI
                payload = {"type": "error", "code": "internal", "message": f"出错了：{e}"}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            finally:
                busy.discard(body.conversation_id)

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    # ---------- notebook ----------

    @app.get("/api/cards")
    async def cards(filter: str = "all", q: str = "", limit: int = 500, offset: int = 0) -> dict[str, Any]:
        if filter not in CARD_FILTERS:
            raise HTTPException(400, "未知的筛选条件")
        limit = max(1, min(limit, 2000))
        return {
            "cards": store.list_cards(filter=filter, query=q, limit=limit, offset=offset),
            "total": store.count_cards(filter=filter, query=q),
            "stats": store.stats(),
        }

    @app.get("/api/cards/{card_id}")
    async def card(card_id: int) -> dict[str, Any]:
        found = store.get_card(card_id)
        if found is None:
            raise HTTPException(404, "这句已经不在句子本里了")
        return found

    @app.patch("/api/cards/{card_id}")
    async def patch_card(card_id: int, body: CardPatch) -> dict[str, Any]:
        if body.zh is not None and not body.zh.strip() or body.en is not None and not body.en.strip():
            raise HTTPException(400, "中文和英文都不能是空的")
        updated = store.update_card(card_id, zh=body.zh, en=body.en)
        if updated is None:
            raise HTTPException(404, "这句已经不在句子本里了")
        return updated

    @app.delete("/api/cards/{card_id}")
    async def delete_card(card_id: int) -> dict[str, Any]:
        return {"ok": store.delete_card(card_id)}

    @app.post("/api/export")
    async def export_csv() -> dict[str, Any]:
        target = Path.home() / "Desktop" / f"句子本-{datetime.now():%Y%m%d-%H%M}.csv"
        rows = store.list_cards(limit=100_000)
        with target.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(["中文", "English", "类型", "熟练度", "添加时间"])
            kinds = {"zh2en": "中译英", "en2zh": "英文解析", "check": "纠错"}
            for c in rows:
                added = datetime.fromtimestamp(c["created_at"]).strftime("%Y-%m-%d %H:%M")
                writer.writerow([c["zh"], c["en"], kinds.get(c["kind"], c["kind"]), c["level"], added])
        return {"path": str(target), "count": len(rows)}

    # ---------- review ----------

    @app.get("/api/review")
    async def review(scope: str = "due", limit: int = 20) -> dict[str, Any]:
        if scope not in REVIEW_SCOPES:
            raise HTTPException(400, "未知的复习范围")
        return {"cards": store.review_queue(scope, max(1, min(limit, 100))), "counts": store.scope_counts()}

    @app.get("/api/review/counts")
    async def review_counts() -> dict[str, Any]:
        return {"counts": store.scope_counts(), "stats": store.stats()}

    @app.post("/api/review/{card_id}")
    async def answer(card_id: int, body: ReviewIn) -> dict[str, Any]:
        updated = store.record_review(card_id, body.remembered)
        if updated is None:
            raise HTTPException(404, "这句已经不在句子本里了")
        return updated

    # ---------- learner memory ----------

    @app.get("/api/notes")
    async def notes() -> dict[str, Any]:
        return {"notes": store.top_notes(50)}

    @app.delete("/api/notes/{note_id}")
    async def delete_note(note_id: int) -> dict[str, Any]:
        store.delete_note(note_id)
        return {"ok": True}

    # ---------- native helpers ----------

    @app.post("/api/speak")
    async def speak(body: TextIn) -> dict[str, Any]:
        text = body.text.strip()[:600]
        proc = speaker["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
        if not text or shutil.which("say") is None:
            return {"ok": False}
        speaker["proc"] = subprocess.Popen(["say", *_english_voice_args(), "-r", "175", text])
        return {"ok": True}

    @app.post("/api/copy")
    async def copy(body: TextIn) -> dict[str, Any]:
        if shutil.which("pbcopy") is None:
            return {"ok": False}
        subprocess.run(["pbcopy"], input=body.text.encode("utf-8"), check=False)
        return {"ok": True}

    @app.post("/api/open-data-dir")
    async def open_data_dir() -> dict[str, Any]:
        config.data_dir().mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["open", str(config.data_dir())])
        return {"ok": True}

    return app
