"""Paths, model choices and user preferences."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME


def data_dir() -> Path:
    override = os.environ.get("SENTENCE_AGENT_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / "Library" / "Application Support" / APP_NAME


def db_path() -> Path:
    return data_dir() / "sentence_agent.db"


@dataclass(frozen=True)
class ModelInfo:
    id: str
    label: str
    note: str
    input_per_mtok: float
    output_per_mtok: float
    # Server-side refusal fallbacks ("fallbacks": "default") are only sent for models that support them.
    server_fallbacks: bool


MODELS: tuple[ModelInfo, ...] = (
    ModelInfo("claude-opus-5", "Claude Opus 5", "推荐：讲解质量和速度最均衡", 5.0, 25.0, True),
    ModelInfo("claude-sonnet-5", "Claude Sonnet 5", "更快、更便宜，讲解稍简单", 2.0, 10.0, False),
    ModelInfo("claude-fable-5-1", "Claude Fable 5.1", "最强的模型，也最贵、最慢", 10.0, 50.0, True),
)
MODELS_BY_ID = {m.id: m for m in MODELS}
DEFAULT_MODEL = "claude-opus-5"

EFFORTS = (
    ("low", "快", "几乎不思考，回复最快"),
    ("medium", "均衡", "推荐：日常查句子够用"),
    ("high", "深入", "想得更久，适合难句和细微的用法差别"),
)
EFFORT_IDS = tuple(e[0] for e in EFFORTS)
DEFAULT_EFFORT = "medium"

# Past this many input tokens a conversation gets expensive to continue; the app suggests a new one.
LONG_CONVERSATION_TOKENS = 120_000

ANTHROPIC_OFFICIAL_URL = "https://api.anthropic.com"
PROTOCOLS = ("anthropic", "openai")


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    protocol: str
    base_url: str
    note: str


# Starting points for the "add provider" form. Every field stays editable; model names come from the
# provider's own model list (fetched with the key) rather than being hard-coded here.
PRESETS: tuple[Preset, ...] = (
    Preset("anthropic", "Anthropic 官方", "anthropic", "", "Claude 官方接口，功能最全（思考过程、缓存、拒答兜底）"),
    Preset("openai", "OpenAI", "openai", "https://api.openai.com/v1", ""),
    Preset("deepseek", "DeepSeek", "openai", "https://api.deepseek.com", ""),
    Preset("qwen", "通义千问（阿里云百炼）", "openai", "https://dashscope.aliyuncs.com/compatible-mode/v1", ""),
    Preset("kimi", "Kimi（月之暗面）", "openai", "https://api.moonshot.cn/v1", ""),
    Preset("glm", "智谱 GLM", "openai", "https://open.bigmodel.cn/api/paas/v4", ""),
    Preset("siliconflow", "硅基流动", "openai", "https://api.siliconflow.cn/v1", ""),
    Preset("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1", ""),
    Preset("gemini", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai/", ""),
    Preset("relay-openai", "中转站（OpenAI 格式）", "openai", "", "填中转站给的接口地址，一般以 /v1 结尾"),
    Preset("relay-anthropic", "中转站（Anthropic 格式）", "anthropic", "", "填中转站给的 Anthropic 接口地址，一般不带 /v1"),
)


def estimate_cost(model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> float:
    info = MODELS_BY_ID.get(model)
    if info is None:
        return 0.0
    per_in = info.input_per_mtok / 1_000_000
    per_out = info.output_per_mtok / 1_000_000
    return input_tokens * per_in + cache_write * per_in * 1.25 + cache_read * per_in * 0.1 + output_tokens * per_out
