"""Fetch and normalize Autosage resources into display-ready shapes.

All backend-shape knowledge (envelope quirks, field names, pagination) lives here,
so the UI code just asks for "the list of workflow names" and gets a clean list.
"""

import httpx

from sagex.api.client import ApiClient, ApiError
from sagex.formatting import relative_time, truncate_name


def _as_list(data) -> list:
    """List endpoints may return a plain list OR a paginated {results: [...]} dict."""
    if isinstance(data, dict):
        return data.get("results", [])
    return data or []


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
            relative_time(r.get("created_at")),
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
             "hint": f"modified {relative_time(w.get('modified_at'))}"}
            for w in hits
        ])
    return client.get(f"/api/workflows/{hits[0]['id']}/")


def get_workflow_detail(client: ApiClient, workflow_id) -> dict:
    """Fetch a workflow's full detail (nodes + edges) by id, skipping name resolution."""
    return client.get(f"/api/workflows/{workflow_id}/")


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
             "hint": f"v{s.get('version')} · updated {relative_time(s.get('updated_at'))}"}
            for s in hits
        ])
    return client.get(f"/api/scripts/{hits[0]['id']}/")


def get_script_content(client: ApiClient, script_id) -> dict:
    """Fetch a script's raw code: {id, name, content, content_type, version}."""
    return client.get(f"/api/scripts/{script_id}/content/")


def resolve_run(client: ApiClient, ref: str) -> dict:
    """Resolve a run by full id, or by an id PREFIX (runs have no names).

    A full UUID is fetched directly; a shorter prefix is matched against the
    recent-runs list (handy for pasting the first few characters).
    """
    ref = ref.strip()
    if len(ref) == 36 and ref.count("-") == 4:          # looks like a full UUID
        try:
            return client.get(f"/api/execution-engine/workflows/runs/{ref}/")
        except ApiError as exc:
            if exc.status == 404:
                raise ResourceNotFound("run", ref)
            raise
    runs = _as_list(client.get("/api/execution-engine/workflows/runs/"))
    hits = [r for r in runs if str(r.get("id", "")).startswith(ref)]
    if not hits:
        raise ResourceNotFound("run", ref)
    if len(hits) > 1:
        raise AmbiguousResource("run", ref, [
            {"id": r.get("id"), "name": r.get("workflow_name") or "(workflow)",
             "hint": f"{r.get('status')} · {relative_time(r.get('created_at'))}"}
            for r in hits
        ])
    return client.get(f"/api/execution-engine/workflows/runs/{hits[0]['id']}/")


def get_run_nodes(client: ApiClient, run_id) -> list[dict]:
    """Per-node results for a run, each with signed log URLs + a `logs_expired` flag."""
    return _as_list(client.get(f"/api/execution-engine/workflows/runs/{run_id}/nodes/"))


def fetch_log_text(url: str) -> str:
    """Download a signed GCS log URL directly (no API key / envelope) and return text.

    The `*_signed_url` fields point straight at Google Cloud Storage, so this is a
    plain GET — not routed through ApiClient.
    """
    if not url:
        return ""
    try:
        resp = httpx.get(url, timeout=30.0)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise ApiError(f"couldn't download log ({exc})")
    return resp.text


def resolve_trigger(client: ApiClient, ref: str) -> tuple[str, dict]:
    """Resolve a trigger by id or workflow name; return (kind, detail).

    `kind` is "http" or "schedule". Detail comes from the richer per-node endpoint
    (adds rotated_at / last_run_id), merged with the aggregate item so we keep
    workflow_name. Only ever exposes `secret_last4`, never a full secret.
    """
    data = client.get("/api/triggers/") or {}
    items = [{**t, "_kind": "http"} for t in data.get("http_triggers", [])]
    items += [{**t, "_kind": "schedule"} for t in data.get("schedule_triggers", [])]

    hits = _match(items, ref, name_key="workflow_name")
    if not hits:
        raise ResourceNotFound("trigger", ref)
    if len(hits) > 1:
        raise AmbiguousResource("trigger", ref, [
            {"id": t.get("id"), "name": f"{t.get('workflow_name')} ({t['_kind']})",
             "hint": f"node {t.get('node_id')}"}
            for t in hits
        ])

    t = hits[0]
    kind = t["_kind"]
    path = f"/api/workflows/{t.get('workflow_id')}/triggers/{kind}/{t.get('node_id')}/"
    try:
        detail = client.get(path) or {}
    except ApiError:
        detail = {}                                     # fall back to the aggregate item
    return kind, {**t, **detail}                        # detail wins; keeps workflow_name


def _vault_name(client: ApiClient, vault_id) -> str | None:
    """Look up a vault's display name from its id (best-effort; None on failure).

    Credentials/servers only carry the vault's UUID; we resolve the friendly name
    with one targeted call so callers can show it instead of the raw id.
    """
    if not vault_id:
        return None
    try:
        return (client.get(f"/api/vault/vaults/{vault_id}/") or {}).get("name")
    except ApiError:
        return None


def resolve_credential(client: ApiClient, ref: str) -> dict:
    """Resolve a vault credential (key) by id or name; returns the MASKED record.

    Secret fields are write-only server-side, so this never contains plaintext.
    Use `reveal_credential` (explicitly) to fetch the actual secret values.
    Enriched with `vault_name` for display.
    """
    items = _as_list(client.get("/api/vault/credentials/"))
    hits = _match(items, ref)
    if not hits:
        raise ResourceNotFound("key", ref)
    if len(hits) > 1:
        raise AmbiguousResource("key", ref, [
            {"id": c.get("id"), "name": c.get("name") or "(unnamed)",
             "hint": c.get("credential_type") or ""}
            for c in hits
        ])
    cred = client.get(f"/api/vault/credentials/{hits[0]['id']}/")
    cred["vault_name"] = _vault_name(client, cred.get("vault"))
    return cred


def reveal_credential(client: ApiClient, credential_id) -> dict:
    """Fetch a credential's PLAINTEXT secrets. Only call on explicit user request.

    This hits the single endpoint that decrypts secrets; the caller must confirm
    with the user first.
    """
    return client.get(f"/api/vault/credentials/{credential_id}/reveal/")


def resolve_server(client: ApiClient, ref: str) -> dict:
    """Resolve a vault server by id or name; returns its detail (enriched).

    Adds `vault_name`; the linked credential's name is already in
    `credential_details` (masked — no secrets).
    """
    items = _as_list(client.get("/api/vault/servers/"))
    hits = _match(items, ref)
    if not hits:
        raise ResourceNotFound("server", ref)
    if len(hits) > 1:
        raise AmbiguousResource("server", ref, [
            {"id": s.get("id"), "name": s.get("name") or "(unnamed)",
             "hint": s.get("host") or ""}
            for s in hits
        ])
    server = client.get(f"/api/vault/servers/{hits[0]['id']}/")
    server["vault_name"] = _vault_name(client, server.get("vault"))
    return server


def resolve_vault(client: ApiClient, ref: str) -> dict:
    """Resolve a vault by id or name; returns its detail (nested creds + servers)."""
    items = _as_list(client.get("/api/vault/vaults/"))
    hits = _match(items, ref)
    if not hits:
        raise ResourceNotFound("vault", ref)
    if len(hits) > 1:
        raise AmbiguousResource("vault", ref, [
            {"id": v.get("id"), "name": v.get("name") or "(unnamed)",
             "hint": f"modified {relative_time(v.get('modified_at'))}"}
            for v in hits
        ])
    return client.get(f"/api/vault/vaults/{hits[0]['id']}/")


# ---------------------------------------------------------------------------
# Full-list helpers for the `sagex list` commands.
# ---------------------------------------------------------------------------


def list_workflows_full(client: ApiClient) -> list[dict]:
    """All workflows — id, name, modified_at."""
    return _as_list(client.get("/api/workflows/"))


def list_scripts_full(client: ApiClient) -> list[dict]:
    """All scripts — id, name, content_type, version."""
    return _as_list(client.get("/api/scripts/"))


def list_runs_full(client: ApiClient, limit: int | None = 20) -> list[dict]:
    """Recent runs sorted newest-first. `limit=None` returns the full history."""
    runs = _as_list(client.get("/api/execution-engine/workflows/runs/"))
    runs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return runs if limit is None else runs[:limit]


def list_triggers_full(client: ApiClient) -> list[dict]:
    """All triggers as a flat list; each dict has a 'kind' key ('http' or 'schedule')."""
    data = client.get("/api/triggers/") or {}
    out: list[dict] = []
    for t in data.get("schedule_triggers", []):
        out.append({**t, "kind": "schedule"})
    for t in data.get("http_triggers", []):
        out.append({**t, "kind": "http"})
    return out


def list_credentials_full(client: ApiClient) -> list[dict]:
    """All credentials enriched with vault_name (2 API calls: creds + vaults list)."""
    creds = _as_list(client.get("/api/vault/credentials/"))
    vault_names = {
        str(v.get("id")): v.get("name")
        for v in _as_list(client.get("/api/vault/vaults/"))
    }
    for c in creds:
        c["vault_name"] = vault_names.get(str(c.get("vault") or ""))
    return creds


def list_servers_full(client: ApiClient) -> list[dict]:
    """All servers — name, host, port, connection_method."""
    return _as_list(client.get("/api/vault/servers/"))


def list_vaults_full(client: ApiClient) -> list[dict]:
    """All vaults (nested credentials + servers included by the serializer)."""
    return _as_list(client.get("/api/vault/vaults/"))
