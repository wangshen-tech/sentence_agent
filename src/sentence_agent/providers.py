"""Model providers the agent can talk to.

A provider is an endpoint (official Anthropic, any OpenAI-compatible API, or a relay speaking either
format), a model name, and a key in the Keychain. Several can be configured; one is active.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from typing import Any, Callable

from . import config, credentials
from .store import Store

_SETTINGS_KEY = "providers"
_ACTIVE_KEY = "active_provider"


@dataclass
class Provider:
    id: str
    name: str
    protocol: str  # "anthropic" | "openai"
    base_url: str  # "" = the official Anthropic endpoint
    model: str

    @property
    def official_anthropic(self) -> bool:
        """The real Anthropic API: every feature on. Anything else gets the conservative request shape."""
        return self.protocol == "anthropic" and self.base_url.rstrip("/") in ("", config.ANTHROPIC_OFFICIAL_URL)

    def public(self, key: str | None, source: str | None) -> dict[str, Any]:
        return {
            **asdict(self),
            "official": self.official_anthropic,
            "has_key": bool(key),
            "key_hint": credentials.key_hint(key),
            "key_source": source,
            "ready": bool(key and self.model),
        }


def normalize_base_url(protocol: str, base_url: str) -> str:
    """Trim whitespace and trailing slashes. The Anthropic SDK adds /v1 itself, so drop a pasted one."""
    url = base_url.strip().rstrip("/")
    if protocol == "anthropic" and url.endswith("/v1"):
        url = url[:-3]
    return url


async def list_models(protocol: str, base_url: str, key: str) -> list[str]:
    """Model ids the endpoint offers. Doubles as a connection and key check."""
    from anthropic import AsyncAnthropic
    from openai import AsyncOpenAI

    base_url = normalize_base_url(protocol, base_url)
    if protocol == "anthropic":
        async with AsyncAnthropic(api_key=key, base_url=base_url or None, max_retries=0, timeout=20) as client:
            ids = [m.id async for m in client.models.list(limit=100)]
    else:
        async with AsyncOpenAI(api_key=key, base_url=base_url, max_retries=0, timeout=20) as client:
            ids = [m.id async for m in client.models.list()]
    return sorted(set(ids))


def _default_provider(store: Store) -> Provider:
    # Carry over the model chosen before multi-provider support existed.
    model = store.get_setting("model", config.DEFAULT_MODEL) or config.DEFAULT_MODEL
    return Provider("anthropic", "Anthropic 官方", "anthropic", "", model)


class ProviderRegistry:
    def __init__(
        self,
        store: Store,
        get_key: Callable[[str], str | None] = credentials.get_key,
        key_source: Callable[[str], str | None] = credentials.key_source,
    ):
        self.store = store
        self._get_key = get_key
        self._key_source = key_source

    # ---------- persistence ----------

    def all(self) -> list[Provider]:
        raw = self.store.get_setting(_SETTINGS_KEY)
        if raw:
            try:
                return [Provider(**p) for p in json.loads(raw)]
            except (TypeError, ValueError):
                pass
        providers = [_default_provider(self.store)]
        self._save(providers)
        return providers

    def _save(self, providers: list[Provider]) -> None:
        self.store.set_setting(_SETTINGS_KEY, json.dumps([asdict(p) for p in providers], ensure_ascii=False))

    def get(self, provider_id: str) -> Provider | None:
        return next((p for p in self.all() if p.id == provider_id), None)

    def active(self) -> Provider:
        providers = self.all()
        active_id = self.store.get_setting(_ACTIVE_KEY)
        return next((p for p in providers if p.id == active_id), providers[0])

    def activate(self, provider_id: str) -> Provider:
        provider = self.get(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        self.store.set_setting(_ACTIVE_KEY, provider_id)
        return provider

    def key(self, provider_id: str) -> str | None:
        return self._get_key(provider_id)

    def describe(self) -> list[dict[str, Any]]:
        return [p.public(self._get_key(p.id), self._key_source(p.id)) for p in self.all()]

    # ---------- editing ----------

    @staticmethod
    def validate(name: str, protocol: str, base_url: str, model: str) -> tuple[str, str, str, str]:
        name, model = name.strip(), model.strip()
        base_url = normalize_base_url(protocol, base_url)
        if protocol not in config.PROTOCOLS:
            raise ValueError("接口格式只能是 OpenAI 兼容或 Anthropic")
        if not name:
            raise ValueError("给这个服务商起个名字")
        if protocol == "openai" and not base_url:
            raise ValueError("OpenAI 兼容格式需要填接口地址")
        if base_url and not base_url.startswith(("https://", "http://")):
            raise ValueError("接口地址要以 https:// 或 http:// 开头")
        if not model:
            raise ValueError("填一个模型名")
        return name, protocol, base_url, model

    def create(self, name: str, protocol: str, base_url: str, model: str) -> Provider:
        name, protocol, base_url, model = self.validate(name, protocol, base_url, model)
        provider = Provider(secrets.token_hex(4), name, protocol, base_url, model)
        self._save([*self.all(), provider])
        return provider

    def update(self, provider_id: str, name: str, protocol: str, base_url: str, model: str) -> Provider:
        name, protocol, base_url, model = self.validate(name, protocol, base_url, model)
        providers = self.all()
        for p in providers:
            if p.id == provider_id:
                p.name, p.protocol, p.base_url, p.model = name, protocol, base_url, model
                self._save(providers)
                return p
        raise KeyError(provider_id)

    def set_model(self, provider_id: str, model: str) -> Provider:
        provider = self.get(provider_id)
        if provider is None:
            raise KeyError(provider_id)
        return self.update(provider_id, provider.name, provider.protocol, provider.base_url, model)

    def delete(self, provider_id: str) -> None:
        providers = self.all()
        if len(providers) <= 1:
            raise ValueError("至少要保留一个服务商")
        remaining = [p for p in providers if p.id != provider_id]
        if len(remaining) == len(providers):
            raise KeyError(provider_id)
        self._save(remaining)
        if self.store.get_setting(_ACTIVE_KEY) == provider_id:
            self.store.set_setting(_ACTIVE_KEY, remaining[0].id)
