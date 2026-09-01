"""Secure storage for the API key using the OS keychain (via `keyring`).

Windows Credential Manager / macOS Keychain / Linux Secret Service — the key
lives in the OS's secret store, never in a plaintext file.
"""

import keyring

_SERVICE = "sagex-cli"
_ACCOUNT = "api_key"


def get_key() -> str | None:
    """Return the stored API key, or None if none is stored / keychain is unavailable."""
    try:
        return keyring.get_password(_SERVICE, _ACCOUNT)
    except Exception:
        return None


def set_key(key: str) -> None:
    """Store the API key in the OS keychain."""
    keyring.set_password(_SERVICE, _ACCOUNT, key)


def delete_key() -> None:
    """Remove the stored API key. No error if nothing was stored."""
    try:
        keyring.delete_password(_SERVICE, _ACCOUNT)
    except Exception:
        pass
