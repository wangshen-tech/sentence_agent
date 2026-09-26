"""API keys live in the macOS Keychain, one entry per provider — never in the database or on disk in plain text."""

from __future__ import annotations

import os

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from . import APP_NAME

_SERVICE = APP_NAME
# The official Anthropic provider keeps the account name used before multi-provider support.
_LEGACY_ANTHROPIC_ACCOUNT = "anthropic-api-key"


class CredentialError(RuntimeError):
    pass


def _account(provider_id: str) -> str:
    return _LEGACY_ANTHROPIC_ACCOUNT if provider_id == "anthropic" else f"provider:{provider_id}"


def get_key(provider_id: str) -> str | None:
    """Keychain first; the official Anthropic provider also falls back to ANTHROPIC_API_KEY."""
    try:
        key = keyring.get_password(_SERVICE, _account(provider_id))
    except KeyringError:
        key = None
    if not key and provider_id == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
    return key or None


def key_source(provider_id: str) -> str | None:
    try:
        if keyring.get_password(_SERVICE, _account(provider_id)):
            return "keychain"
    except KeyringError:
        pass
    if provider_id == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return "env"
    return None


def set_key(provider_id: str, key: str) -> None:
    key = key.strip()
    if not key:
        raise CredentialError("API key 不能是空的")
    try:
        keyring.set_password(_SERVICE, _account(provider_id), key)
    except KeyringError as e:
        raise CredentialError(f"没能存进钥匙串：{e}") from e


def delete_key(provider_id: str) -> None:
    try:
        keyring.delete_password(_SERVICE, _account(provider_id))
    except PasswordDeleteError:
        pass
    except KeyringError as e:
        raise CredentialError(f"没能从钥匙串删除：{e}") from e


def key_hint(key: str | None) -> str | None:
    if not key:
        return None
    return f"{key[:6]}…{key[-4:]}" if len(key) > 14 else "已设置"
