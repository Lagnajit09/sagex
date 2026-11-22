"""Which model Sage answers with: your personal models (Autobot's BYO configs)
or Autosage's free models.

Shared by the `sagex … model` commands, the TUI's `/model` and the sidebar.
The selection is account-wide — the same setting as the web's Customize panel —
so choosing here also changes what Autobot on the web uses.
"""

from __future__ import annotations

FREE_LABEL = "Autosage free models"
FREE_NOTE = "shared daily allowance"
SHARED_NOTE = "Also used by Autobot on the web."

# Examples per provider, as the web form shows them.
MODEL_HINTS = {
    "gemini": "gemini/gemini-2.0-flash",
    "groq": "groq/llama-3.3-70b-versatile",
    "openrouter": "openrouter/meta-llama/llama-4-scout-17b-16e-instruct:free",
    "anthropic": "anthropic/claude-sonnet-4-5",
    "openai": "gpt-4o-mini",
    "azure_openai": "your Azure deployment name",
    "nvidia_nim": "nvidia/nemotron-3-super-120b-a12b",
    "custom": "any LiteLLM model id",
}


def model_id(m: dict) -> str:
    return str(m.get("id") or "")


def wire_name(m: dict) -> str:
    """'anthropic/claude-sonnet-4-5' — the model string without a doubled provider."""
    provider = str(m.get("provider") or "").strip()
    name = str(m.get("model_name") or "").strip()
    if not provider or name.startswith(f"{provider}/"):
        return name or provider
    return f"{provider}/{name}"


def label(m: dict | None) -> str:
    """'My Claude · anthropic/claude-sonnet-4-5', or the free models' name."""
    if not m:
        return FREE_LABEL
    return f"{m.get('name') or '(unnamed)'} · {wire_name(m)}"


def find(items: list[dict], chosen: str | None) -> dict | None:
    return next((m for m in items if model_id(m) == chosen), None) if chosen else None


def pick(items: list[dict], ref: str) -> dict | None | str:
    """A `/model` argument → a model, None for the free models, or an error message.

    `ref` is a number from the `/model` list (0 = free models), "free", or a
    model's name.
    """
    ref = ref.strip()
    if ref.lower() in ("0", "free"):
        return None
    if ref.isdigit():
        n = int(ref)
        if 1 <= n <= len(items):
            return items[n - 1]
        return f"No model {n} — /model lists 0–{len(items)}."
    hits = [m for m in items if str(m.get("name") or "").lower() == ref.lower()]
    if hits:
        return hits[0]
    return f"No model named {ref!r} — /model lists yours."
