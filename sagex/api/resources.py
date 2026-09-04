"""Fetch and normalize Autosage resources into display-ready shapes.

All backend-shape knowledge (envelope quirks, field names, pagination) lives here,
so the UI code just asks for "the list of workflow names" and gets a clean list.
"""

from datetime import datetime, timezone

from sagex.api.client import ApiClient
from sagex.formatting import truncate_name


def _as_list(data) -> list:
    """List endpoints may return a plain list OR a paginated {results: [...]} dict."""
    if isinstance(data, dict):
        return data.get("results", [])
    return data or []


def _relative_time(iso: str | None) -> str:
    """Turn an ISO timestamp into a short 'just now' / '2m ago' / '3h ago' / '5d ago'."""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def list_workflows(client: ApiClient) -> list[str]:
    """Return workflow display names (trimmed)."""
    data = client.get("/api/workflows/")
    return [truncate_name(w.get("name") or "(unnamed)") for w in _as_list(data)]


def list_scripts(client: ApiClient) -> list[str]:
    """Return script file names (trimmed)."""
    data = client.get("/api/scripts/")
    return [truncate_name(s.get("name") or "(unnamed)") for s in _as_list(data)]


def list_vault_resources(client: ApiClient) -> list[str]:
    """Return vault items — servers and credentials — labeled by kind.

    Only the name is trimmed; the '(server)'/'(credential)' kind is kept intact.
    """
    servers = _as_list(client.get("/api/vault/servers/"))
    creds = _as_list(client.get("/api/vault/credentials/"))
    labels = [f"{truncate_name(s.get('name') or '(unnamed)')} (server)" for s in servers]
    labels += [f"{truncate_name(c.get('name') or '(unnamed)')} (credential)" for c in creds]
    return labels


def list_triggers(client: ApiClient) -> list[tuple[str, str, bool]]:
    """Return configured triggers as (workflow_name, detail, is_active).

    From the aggregate /api/triggers/ endpoint (one call); data is
    {"http_triggers": [...], "schedule_triggers": [...]}. `detail` is just the
    trigger TYPE — "scheduler" or "http" — kept short on purpose; the cron/URL and
    full details will come from a command later. The endpoint only returns
    *configured* triggers, which is what we want.
    """
    data = client.get("/api/triggers/") or {}
    out: list[tuple[str, str, bool]] = []
    for t in data.get("schedule_triggers", []):
        name = t.get("workflow_name") or "(workflow)"
        out.append((name, "scheduler", bool(t.get("is_active"))))
    for t in data.get("http_triggers", []):
        name = t.get("workflow_name") or "(workflow)"
        out.append((name, "http", bool(t.get("is_active"))))
    return out


def list_recent_runs(client: ApiClient, limit: int = 5) -> list[tuple[str, str, str]]:
    """Return the most recent workflow runs as (status, workflow_name, relative_time)."""
    runs = _as_list(client.get("/api/execution-engine/workflows/runs/"))
    runs.sort(key=lambda r: r.get("created_at") or "", reverse=True)   # newest first
    return [
        (
            r.get("status") or "unknown",
            r.get("workflow_name") or "(workflow)",
            _relative_time(r.get("created_at")),
        )
        for r in runs[:limit]
    ]
