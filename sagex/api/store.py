"""Secure storage for the API key using the OS keychain (via `keyring`).

Windows Credential Manager / macOS Keychain / Linux Secret Service — the key
lives in the OS's secret store, never in a plaintext file.

SAGEX_API_KEY, when set, takes precedence over the keychain. That covers CI jobs
and headless machines with no keychain, without ever writing the key to disk.
"""

import os

import keyring

_SERVICE = "sagex-cli"
_ACCOUNT = "api_key"
ENV_VAR = "SAGEX_API_KEY"


class KeyStoreError(Exception):
    """The OS keychain couldn't store the key (e.g. no backend on a headless box)."""


def key_from_env() -> str | None:
    """The key from SAGEX_API_KEY, or None when it's unset/blank."""
    return os.environ.get(ENV_VAR, "").strip() or None


def get_key() -> str | None:
    """Return the API key (env first, then keychain), or None if there isn't one."""
    env_key = key_from_env()
    if env_key:
        return env_key
    try:
        return keyring.get_password(_SERVICE, _ACCOUNT)
    except Exception:
        return None


def set_key(key: str) -> None:
    """Store the API key in the OS keychain; KeyStoreError if there's no usable one."""
    try:
        keyring.set_password(_SERVICE, _ACCOUNT, key)
    except Exception as exc:          # NoKeyringError, locked/absent Secret Service, …
        raise KeyStoreError(str(exc)) from exc


def delete_key() -> None:
    """Remove the stored API key. No error if nothing was stored."""
    try:
        keyring.delete_password(_SERVICE, _ACCOUNT)
    except Exception:
        pass
