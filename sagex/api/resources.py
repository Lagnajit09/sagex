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


# ---------------------------------------------------------------------------
# Single-resource lookup ("show" / "copy"): resolve a name-or-id reference to
# one record, then fetch its full detail. Shared by the CLI and (later) the TUI.
# ---------------------------------------------------------------------------


class ResourceNotFound(Exception):
    """No resource matched the given reference."""

    def __init__(self, kind: str, ref: str) -> None:
        super().__init__(f"No {kind} matching '{ref}'.")
        self.kind = kind
        self.ref = ref


class AmbiguousResource(Exception):
    """A name matched more than one resource — the caller must pick by id.

    `matches` is a list of {id, name, hint} dicts for display.
    """

    def __init__(self, kind: str, ref: str, matches: list[dict]) -> None:
        super().__init__(f"Multiple {kind}s match '{ref}'.")
        self.kind = kind
        self.ref = ref
        self.matches = matches


def _match(items: list[dict], ref: str, *, id_key: str = "id", name_key: str = "name") -> list[dict]:
    """Find items by EXACT id first (fast, unambiguous), else case-insensitive name.

    Returns every name match so the caller can detect ambiguity (len > 1).
    """
    for it in items:                                  # exact id wins outright
        if str(it.get(id_key)) == ref:
            return [it]
    ref_l = ref.strip().lower()
    return [it for it in items if str(it.get(name_key) or "").lower() == ref_l]


def resolve_workflow(client: ApiClient, ref: str) -> dict:
    """Resolve a workflow name-or-id and return its FULL detail (incl. nodes/edges).

    The list endpoint strips the graph; only the detail endpoint returns it.
    """
    items = _as_list(client.get("/api/workflows/"))
    hits = _match(items, ref)
    if not hits:
        raise ResourceNotFound("workflow", ref)
    if len(hits) > 1:
        raise AmbiguousResource("workflow", ref, [
            {"id": w.get("id"), "name": w.get("name") or "(unnamed)",
             "hint": f"modified {_relative_time(w.get('modified_at'))}"}
            for w in hits
        ])
    return client.get(f"/api/workflows/{hits[0]['id']}/")


def resolve_script(client: ApiClient, ref: str) -> dict:
    """Resolve a script name-or-id and return its metadata record (no code body).

    Script ids are ints (unlike workflows' UUIDs); the code lives in a separate
    content endpoint — see `get_script_content`.
    """
    items = _as_list(client.get("/api/scripts/"))
    hits = _match(items, ref)
    if not hits:
        raise ResourceNotFound("script", ref)
    if len(hits) > 1:
        raise AmbiguousResource("script", ref, [
            {"id": s.get("id"), "name": s.get("name") or "(unnamed)",
             "hint": f"v{s.get('version')} · updated {_relative_time(s.get('updated_at'))}"}
            for s in hits
        ])
    return client.get(f"/api/scripts/{hits[0]['id']}/")


def get_script_content(client: ApiClient, script_id) -> dict:
    """Fetch a script's raw code: {id, name, content, content_type, version}."""
    return client.get(f"/api/scripts/{script_id}/content/")
