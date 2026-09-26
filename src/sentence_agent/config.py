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


def estimate_cost(model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> float:
    info = MODELS_BY_ID.get(model)
    if info is None:
        return 0.0
    per_in = info.input_per_mtok / 1_000_000
    per_out = info.output_per_mtok / 1_000_000
    return input_tokens * per_in + cache_write * per_in * 1.25 + cache_read * per_in * 0.1 + output_tokens * per_out
