"""Write remote Autosage resources to local files in the workspace.

The file-side of the `sagex copy` command: given a client and a resolved resource,
fetch whatever bytes are needed via `sagex.api.resources` and write them under the
workspace, returning the paths written so the caller can report them. No terminal
formatting lives here (that's render.py) — this is reusable by the TUI later.

Layout under the workspace:
    workflows/<name>.json          workflow detail (re-importable: name/description/nodes/edges)
    scripts/<name.ext>             raw code (name already carries the real extension)
    runs/<workflow>__<id8>/        one folder per run
        run.json                   slim run metadata + node summary
        logs.log                   header + per-node stdout/stderr (mirrors the web app)

Secrets are never written: keys/credentials are refused at the CLI layer, and runs
past the 90-day retention window get a note instead of (unavailable) log bytes.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sagex.api import resources
from sagex.api.client import ApiError

# Rolling time windows for `copy run --since` (days). "all" means no cutoff.
WINDOWS = {"1day": 1, "7days": 7, "1month": 30}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str | None, fallback: str) -> str:
    """Filesystem-safe slug: collapse anything outside [A-Za-z0-9._-] to '_'."""
    cleaned = _UNSAFE.sub("_", (name or "").strip()).strip("_")
    return cleaned or fallback


def _write(path: Path, text: str) -> Path:
    """Write text (UTF-8), creating parent folders. Returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- workflow --------------------------------------------------------------

def copy_workflow(workspace: Path, wf: dict) -> Path:
    """Write a workflow's detail as JSON under <ws>/workflows/<name>.json."""
    doc = {k: wf.get(k) for k in
           ("id", "name", "description", "nodes", "edges", "created_at", "modified_at")}
    name = safe_name(wf.get("name"), f"workflow_{wf.get('id')}")
    return _write(workspace / "workflows" / f"{name}.json", json.dumps(doc, indent=2))


def copy_all_workflows(client, workspace: Path) -> list[Path]:
    """Copy every workflow (fetches each one's detail for the full graph)."""
    return [
        copy_workflow(workspace, resources.get_workflow_detail(client, it["id"]))
        for it in resources.list_workflows_full(client)
    ]


# --- script ----------------------------------------------------------------

def copy_script(workspace: Path, meta: dict, content: dict) -> Path:
    """Write a script's code under <ws>/scripts/<name.ext> (name carries the ext)."""
    filename = safe_name(content.get("name") or meta.get("name"), f"script_{meta.get('id')}")
    return _write(workspace / "scripts" / filename, content.get("content") or "")


def copy_all_scripts(client, workspace: Path) -> list[Path]:
    """Copy every script (fetches each one's code body)."""
    paths = []
    for it in resources.list_scripts_full(client):
        content = resources.get_script_content(client, it["id"])
        paths.append(copy_script(workspace, it, content))
    return paths


# --- run / logs ------------------------------------------------------------

def _slim_run(run: dict, nodes: list[dict]) -> dict:
    """Run metadata worth keeping + a compact per-node summary (no signed URLs)."""
    doc = {k: run.get(k) for k in
           ("id", "workflow_name", "status", "created_at", "started_at",
            "finished_at", "error_message", "inputs") if k in run}
    doc["nodes"] = [
        {"order": n.get("execution_order"),
         "label": n.get("node_label") or n.get("node_id"),
         "status": n.get("status"),
         "exit_code": n.get("exit_code"),
         "error": n.get("error_message")}
        for n in sorted(nodes, key=lambda x: x.get("execution_order") or 0)
    ]
    return doc


def _aggregate_logs(run: dict, nodes: list[dict]) -> str:
    """One text blob: header block + per-node stdout/stderr. Mirrors the web app."""
    lines = [
        f"Workflow: {run.get('workflow_name') or '(workflow)'}",
        f"Run ID: {run.get('id')}",
        f"Status: {run.get('status')}",
        f"Started: {run.get('started_at') or '-'}",
        "",
    ]
    for n in sorted(nodes, key=lambda x: x.get("execution_order") or 0):
        label = n.get("node_label") or n.get("node_id") or "node"
        lines.append(f"[NODE: {label}] STATUS: {n.get('status') or '-'}")
        for stream in ("stdout", "stderr"):
            lines.append(f"--- {stream.upper()} ---")
            url = n.get(f"{stream}_signed_url")
            if not url:
                lines.append("(none)")
                continue
            try:
                lines.append(resources.fetch_log_text(url).rstrip())
            except ApiError as exc:
                lines.append(f"(couldn't download {stream}: {exc.message})")
        lines.append("")
    return "\n".join(lines)


def copy_run(client, workspace: Path, run: dict) -> list[Path]:
    """Write <ws>/runs/<workflow>__<id8>/{run.json, logs.log}. Returns paths written."""
    rid = str(run.get("id") or "")
    folder = workspace / "runs" / f"{safe_name(run.get('workflow_name'), 'run')}__{rid[:8]}"

    nodes = resources.get_run_nodes(client, rid)
    written = [_write(folder / "run.json", json.dumps(_slim_run(run, nodes), indent=2))]

    if any(n.get("logs_expired") for n in nodes):
        body = "Logs are not available for this run - we keep logs for 90 days.\n"
    else:
        body = _aggregate_logs(run, nodes)
    written.append(_write(folder / "logs.log", body))
    return written


def runs_in_window(runs: list[dict], window: str) -> list[dict]:
    """Filter runs to those whose `created_at` falls within a rolling window.

    `window` is one of WINDOWS' keys ("1day"/"7days"/"1month") or "all".
    """
    if window == "all":
        return runs
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOWS[window])
    kept = []
    for r in runs:
        try:
            dt = datetime.fromisoformat(r.get("created_at"))
        except (ValueError, TypeError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt >= cutoff:
            kept.append(r)
    return kept
