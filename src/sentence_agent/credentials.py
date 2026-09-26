"""The Anthropic API key lives in the macOS Keychain, never in the database or on disk in plain text."""

from __future__ import annotations

import os

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from . import APP_NAME

_SERVICE = APP_NAME
_ACCOUNT = "anthropic-api-key"


class CredentialError(RuntimeError):
    pass


def get_api_key() -> str | None:
    """Keychain first (what the user entered in the app), then the ANTHROPIC_API_KEY environment variable."""
    try:
        key = keyring.get_password(_SERVICE, _ACCOUNT)
    except KeyringError:
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY") or None


def key_source() -> str | None:
    try:
        if keyring.get_password(_SERVICE, _ACCOUNT):
            return "keychain"
    except KeyringError:
        pass
    return "env" if os.environ.get("ANTHROPIC_API_KEY") else None


def set_api_key(key: str) -> None:
    key = key.strip()
    if not key:
        raise CredentialError("API key 不能是空的")
    try:
        keyring.set_password(_SERVICE, _ACCOUNT, key)
    except KeyringError as e:
        raise CredentialError(f"没能存进钥匙串：{e}") from e


def delete_api_key() -> None:
    try:
        keyring.delete_password(_SERVICE, _ACCOUNT)
    except PasswordDeleteError:
        pass
    except KeyringError as e:
        raise CredentialError(f"没能从钥匙串删除：{e}") from e


def key_hint(key: str | None) -> str | None:
    if not key:
        return None
    return f"{key[:7]}…{key[-4:]}" if len(key) > 14 else "已设置"
