"""Terminal rendering for the `sagex show` commands (Rich console output).

This is the CLI-side presentation layer: it takes the plain data returned by
`sagex.api.resources` and prints it nicely to stdout. The TUI will render the
same data as widgets later; only this file is CLI-specific.
"""

import difflib
from datetime import datetime

from rich.cells import cell_len
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from sagex.formatting import STATUS_ICON, relative_time

console = Console()


def note(text: str) -> None:
    """A dim one-line note (e.g. logs unavailable / hint)."""
    console.print(Text(text, style="bright_black"))


# --- shared helpers --------------------------------------------------------

def print_json(data) -> None:
    """Dump a record as pretty JSON (for the --json flag / piping)."""
    console.print_json(data=data)


def ambiguous(exc) -> None:
    """Print the matches for an ambiguous name and how to disambiguate."""
    console.print(
        f"[yellow]Multiple {exc.kind}s match [bold]{exc.ref}[/bold]. "
        f"Re-run with an id:[/yellow]"
    )
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("name")
    table.add_column("", style="bright_black")          # hint column
    for m in exc.matches:
        table.add_row(str(m.get("id")), m.get("name") or "(unnamed)", m.get("hint") or "")
    console.print(table)


def _kv(label: str, value) -> Text:
    """One 'Label: value' line with a dim label."""
    line = Text()
    line.append(f"{label}: ", style="bright_black")
    line.append("" if value is None else str(value))
    return line


# --- workflow --------------------------------------------------------------

def workflow(wf: dict) -> None:
    """Show a workflow: header panel + a table of its nodes."""
    nodes = wf.get("nodes") or []
    edges = wf.get("edges") or []

    header = Text()
    header.append(_kv("id", wf.get("id")));          header.append("\n")
    header.append(_kv("description", wf.get("description") or "—")); header.append("\n")
    header.append(_kv("nodes", f"{len(nodes)}   edges: {len(edges)}")); header.append("\n")
    header.append(_kv("created", wf.get("created_at"))); header.append("\n")
    header.append(_kv("modified", wf.get("modified_at")))
    console.print(Panel(header, title=wf.get("name") or "(unnamed workflow)", title_align="left"))

    if nodes:
        table = Table(title="Nodes", show_header=True, header_style="bold", title_justify="left")
        table.add_column("id", style="cyan", no_wrap=True)
        table.add_column("type")
        table.add_column("label")
        for n in nodes:
            data = n.get("data") if isinstance(n.get("data"), dict) else {}
            label = data.get("label") or n.get("label") or ""
            table.add_row(str(n.get("id") or ""), str(n.get("type") or ""), str(label))
        console.print(table)


# --- workflow graph (text visualization) -----------------------------------

# icon + colour + short kind label, keyed off node type (and action's data.type).
def _node_style(node: dict) -> tuple[str, str, str]:
    ntype = node.get("type")
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    dtype = data.get("type")
    if ntype == "trigger":
        return "▸", "cyan", f"trigger:{dtype or 'manual'}"
    if ntype == "decision":
        return "◆", "yellow", "decision"
    if ntype == "action":
        if dtype == "email":
            return "■", "blue", "action:email"
        if dtype == "script":
            return "●", "green", "action:script"
        return "•", "white", f"action:{dtype or '?'}"
    return "•", "white", str(ntype or "?")


def _node_box(node: dict | None, node_id) -> tuple[list[Text], int]:
    """Draw a node as a rounded rectangle. Returns (lines, box_width_in_cells).

    The icon glyphs (⚡⚙✉◆) are counted as one cell each — Rich's cell_len calls
    some of them width-2 (emoji), but terminals render them width-1, so we measure
    the head line as icon(1) + space + label to keep the right border aligned.
    """
    if node is None:
        icon, color, kind = "•", "red", "missing node"
        label = str(node_id)
    else:
        icon, color, kind = _node_style(node)
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        label = str(data.get("label") or node.get("label") or node_id)
    if len(label) > 42:
        label = label[:41] + "…"

    meta = f"{kind}  {node_id}" if str(node_id) != label else kind
    head_w = 2 + cell_len(label)                 # icon(1) + space(1) + label
    inner = max(head_w, cell_len(meta))

    def _row(prefix_runs, width, close=True):
        row = Text("│ ", style=color)
        for text, style in prefix_runs:
            row.append(text, style=style)
        row.append(" " * (inner - width) + " ", style=color)
        row.append("│", style=color)
        return row

    return (
        [
            Text("╭" + "─" * (inner + 2) + "╮", style=color),
            _row([(f"{icon} ", color), (label, color)], head_w),
            _row([(meta, "bright_black")], cell_len(meta)),
            Text("╰" + "─" * (inner + 2) + "╯", style=color),
        ],
        inner + 4,
    )


def _prefix(lines: list[Text], first: str, rest: str, style: str = "bright_black") -> list[Text]:
    """Left-attach a connector to a block: `first` on line 0, `rest` on the others."""
    out = []
    for i, line in enumerate(lines):
        row = Text(first if i == 0 else rest, style=style)
        row.append(line)
        out.append(row)
    return out


def workflow_graph(wf: dict) -> None:
    """Render a workflow as boxed nodes joined by flow edges.

    Linear steps stack vertically under a centered spine (│▼); a decision fans out
    to labelled elbow connectors (├─ true ▶ / └─ false ▶). A node reached by two
    paths is drawn once and shown later as a back-reference (↑), keeping it finite.
    """
    nodes = wf.get("nodes") or []
    edges = wf.get("edges") or []
    if not nodes:
        note("(workflow has no nodes)")
        return

    by_id = {n.get("id"): n for n in nodes}
    children: dict = {}
    indeg = {n.get("id"): 0 for n in nodes}
    for e in edges:
        children.setdefault(e.get("source"), []).append(e)
        if e.get("target") in indeg:
            indeg[e.get("target")] += 1

    roots = [n.get("id") for n in nodes if n.get("type") == "trigger"]
    if not roots:
        roots = [nid for nid, d in indeg.items() if d == 0] or [nodes[0].get("id")]

    visited: set = set()

    def block(node_id) -> list[Text]:
        """The subtree rooted at node_id, box top-left anchored at column 0."""
        lines, width = _node_box(by_id.get(node_id), node_id)
        visited.add(node_id)
        kids = children.get(node_id, [])

        if len(kids) == 1 and (by_id.get(node_id) or {}).get("type") != "decision":
            spine = width // 2
            lines.append(Text(" " * spine + "│", style="bright_black"))
            lines.append(Text(" " * spine + "▼", style="bright_black"))
            lines += _child(kids[0])
            return lines

        for i, e in enumerate(kids):
            last = i == len(kids) - 1
            handle = e.get("sourceHandle")
            tag = f"{handle} " if handle else ""
            first = ("└─ " if last else "├─ ") + f"{tag}▶ "
            rest = (" " if last else "│") + " " * (len(first) - 1)
            lines += _prefix(_child(e), first, rest)
        return lines

    def _child(edge) -> list[Text]:
        target = edge.get("target")
        if target in visited:
            label = (by_id.get(target) or {}).get("data", {})
            name = label.get("label") if isinstance(label, dict) else None
            return [Text(f"↑ {name or target} (shown above)", style="bright_black")]
        return block(target)

    title = Text(wf.get("name") or "(workflow)", style="bold")
    title.append(f"   {len(nodes)} nodes · {len(edges)} edges", style="bright_black")
    console.print(title)

    for r in roots:
        for line in block(r):
            console.print(line)

    leftover = [n.get("id") for n in nodes if n.get("id") not in visited]
    if leftover:
        console.print(Text("(not reachable from a trigger)", style="bright_black"))
        for nid in leftover:
            if nid not in visited:
                for line in block(nid):
                    console.print(line)

    console.print(Text("▸ trigger   ● script   ■ email   ◆ decision", style="bright_black"))


# --- script ----------------------------------------------------------------

_EXT_LEXER = {
    "py": "python", "js": "javascript", "ts": "typescript", "sh": "bash",
    "bash": "bash", "ps1": "powershell", "rb": "ruby", "go": "go",
    "json": "json", "yml": "yaml", "yaml": "yaml", "sql": "sql",
}


def _lexer_for(name: str, content_type: str) -> str:
    """Best-effort syntax lexer from the file extension, then the MIME type."""
    if name and "." in name:
        ext = name.rsplit(".", 1)[-1].lower()
        if ext in _EXT_LEXER:
            return _EXT_LEXER[ext]
    ct = (content_type or "").lower()
    if "python" in ct:
        return "python"
    if "javascript" in ct:
        return "javascript"
    if "powershell" in ct:
        return "powershell"
    if "sh" in ct or "bash" in ct:
        return "bash"
    return "text"


def script(meta: dict, content: dict | None) -> None:
    """Show a script: metadata panel + syntax-highlighted code (if fetched)."""
    header = Text()
    header.append(_kv("id", meta.get("id")));               header.append("\n")
    header.append(_kv("pathname", meta.get("pathname") or "—")); header.append("\n")
    header.append(_kv("type", meta.get("content_type")));   header.append("\n")
    header.append(_kv("size", f"{meta.get('file_size')} bytes  ·  v{meta.get('version')}")); header.append("\n")
    header.append(_kv("updated", meta.get("updated_at")))
    console.print(Panel(header, title=meta.get("name") or "(unnamed script)", title_align="left"))

    if content is not None:
        code = content.get("content") or ""
        lexer = _lexer_for(content.get("name") or meta.get("name") or "",
                           content.get("content_type") or meta.get("content_type") or "")
        console.print(Syntax(code, lexer, theme="ansi_dark", line_numbers=True, word_wrap=True))


def script_diff(name: str, server_code: str, local_code: str) -> bool:
    """Print a unified diff of server→local script code. Returns True if they differ.

    `-` lines are the current server version (about to be overwritten), `+` lines are
    the local file. Used by `push script` to preview an update before it bumps the
    version. Line-based; a difference in trailing newline alone is treated as no change.
    """
    diff = list(difflib.unified_diff(
        server_code.splitlines(), local_code.splitlines(),
        fromfile=f"server:{name}", tofile=f"local:{name}", lineterm="",
    ))
    if not diff:
        return False
    for line in diff:
        if line.startswith(("+++", "---")):
            console.print(Text(line, style="bold"))
        elif line.startswith("@@"):
            console.print(Text(line, style="cyan"))
        elif line.startswith("+"):
            console.print(Text(line, style="green"))
        elif line.startswith("-"):
            console.print(Text(line, style="red"))
        else:
            console.print(Text(line, style="bright_black"))
    return True


# --- run -------------------------------------------------------------------

def _duration(started, finished) -> str | None:
    """Human 'finished - started' duration, or None if not computable."""
    if not started or not finished:
        return None
    try:
        secs = (datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds()
    except ValueError:
        return None
    if secs < 0:
        return None
    if secs < 60:
        return f"{secs:.1f}s"
    minutes, sec = divmod(int(secs), 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def run(r: dict, nodes: list[dict]) -> None:
    """Show a run: header panel + a per-node results table."""
    status = r.get("status") or "unknown"
    icon, color = STATUS_ICON.get(status, ("•", "white"))

    header = Text()
    header.append(_kv("id", r.get("id")));       header.append("\n")
    st = Text(); st.append("status: ", style="bright_black"); st.append(f"{icon} {status}", style=color)
    header.append(st);                           header.append("\n")
    header.append(_kv("created", r.get("created_at")));    header.append("\n")
    header.append(_kv("started", r.get("started_at") or "—"));   header.append("\n")
    header.append(_kv("finished", r.get("finished_at") or "—")); header.append("\n")
    header.append(_kv("duration", _duration(r.get("started_at"), r.get("finished_at")) or "—"))
    if r.get("inputs"):
        header.append("\n"); header.append(_kv("inputs", r.get("inputs")))
    if r.get("error_message"):
        header.append("\n")
        err = Text(); err.append("error: ", style="bright_black"); err.append(str(r.get("error_message")), style="red")
        header.append(err)
    console.print(Panel(header, title=r.get("workflow_name") or "(run)", title_align="left"))

    if nodes:
        ordered = sorted(nodes, key=lambda n: n.get("execution_order") or 0)
        table = Table(title="Nodes", show_header=True, header_style="bold", title_justify="left")
        table.add_column("#", style="bright_black", no_wrap=True)
        table.add_column("node")
        table.add_column("status")
        table.add_column("exit", justify="right")
        table.add_column("error")
        for n in ordered:
            nstatus = n.get("status") or ""
            _, ncolor = STATUS_ICON.get(nstatus, ("•", "white"))
            code = n.get("exit_code")
            table.add_row(
                str(n.get("execution_order") if n.get("execution_order") is not None else ""),
                str(n.get("node_label") or n.get("node_id") or ""),
                Text(nstatus, style=ncolor),
                "" if code is None else str(code),
                str(n.get("error_message") or ""),
            )
        console.print(table)


def run_logs(entries: list[tuple[str, str, str]]) -> None:
    """Dump collected log bodies: entries are (node_label, stream, text)."""
    if not entries:
        note("(no log output)")
        return
    for label, stream, text in entries:
        body = Text(text.rstrip() or "(empty)")
        console.print(Panel(body, title=f"{label} · {stream}", title_align="left", border_style="bright_black"))


# --- trigger ---------------------------------------------------------------

def trigger(kind: str, t: dict) -> None:
    """Show a trigger's detail. Only ever displays the last 4 of a secret."""
    header = Text()
    header.append(_kv("kind", kind));               header.append("\n")
    header.append(_kv("node", t.get("node_id")));   header.append("\n")
    st = Text(); st.append("status: ", style="bright_black")
    if t.get("is_active"):
        st.append("● enabled", style="green")
    else:
        st.append("○ disabled", style="bright_black")
    header.append(st);                              header.append("\n")
    header.append(_kv("created", t.get("created_at")));  header.append("\n")
    header.append(_kv("last triggered", t.get("last_triggered_at") or "never"))

    if kind == "http":
        header.append("\n"); header.append(_kv("url", t.get("trigger_url")))
        last4 = t.get("secret_last4")
        header.append("\n"); header.append(_kv("secret", f"••••{last4}" if last4 else "—"))
        if t.get("rotated_at"):
            header.append("\n"); header.append(_kv("rotated", t.get("rotated_at")))
    else:
        header.append("\n"); header.append(_kv("cron", t.get("cron_expression")))
        desc = describe_cron(t.get("cron_expression"))
        if desc:
            header.append("\n"); header.append(_kv("when", f"{desc} (UTC)"))
        header.append("\n"); header.append(_kv("timezone", t.get("timezone")))
        if t.get("last_run_id"):
            header.append("\n"); header.append(_kv("last run", t.get("last_run_id")))
        if t.get("last_error"):
            header.append("\n")
            err = Text(); err.append("last error: ", style="bright_black"); err.append(str(t.get("last_error")), style="red")
            header.append(err)

    console.print(Panel(header, title=t.get("workflow_name") or "(trigger)", title_align="left"))


# --- trigger writes (create / regenerate / invoke) -------------------------

def _http_invocation(url: str, secret_display: str) -> None:
    """Print a copy-paste 'how to call this HTTP trigger' cheat-sheet."""
    body = '{"inputs": {}}'
    block = Text()
    block.append("POST ", style="bold"); block.append(str(url)); block.append("\n")
    block.append("Headers:\n", style="bright_black")
    block.append("  X-Trigger-Secret: ", style="bright_black"); block.append(secret_display); block.append("\n")
    block.append("  Idempotency-Key:  ", style="bright_black"); block.append("<unique per request — required>"); block.append("\n")
    block.append("  Content-Type:     ", style="bright_black"); block.append("application/json"); block.append("\n")
    block.append("Body:\n", style="bright_black")
    block.append(f"  {body}")
    console.print(Panel(block, title="Call this HTTP trigger", title_align="left", border_style="cyan"))

    curl = (
        f'curl -X POST "{url}" \\\n'
        f'  -H "X-Trigger-Secret: {secret_display}" \\\n'
        f'  -H "Idempotency-Key: $(uuidgen)" \\\n'
        f'  -H "Content-Type: application/json" \\\n'
        f'  -d \'{body}\''
    )
    # soft_wrap so long URLs aren't hard-broken mid-token — keeps the curl copy-pasteable.
    console.print(Text(curl, style="bright_black"), soft_wrap=True)
    note("Response 202 → { workflow_run_id, status, polling_url }")


def http_trigger_result(data: dict) -> None:
    """After create/regenerate: show the plaintext secret ONCE + the invocation cheat-sheet."""
    secret = data.get("secret")
    console.print(Text("✓ HTTP trigger ready.", style="green"))
    if secret:
        console.print(Text("Secret — shown once, copy it now (it can't be retrieved again):", style="bold red"))
        console.print(Text(f"  {secret}", style="bold"))
    _http_invocation(data.get("trigger_url"), secret or f"<your secret, ends ••••{data.get('secret_last4')}>")


def http_trigger_invocation(data: dict) -> None:
    """Reprint how to call an existing HTTP trigger (secret not shown — only last4)."""
    if not data.get("is_active", True):
        note("This trigger is disabled — enable it before calling:  sagex trigger enable …")
    _http_invocation(data.get("trigger_url"), f"<your secret, ends ••••{data.get('secret_last4')}>")
    note("The secret is stored hashed and can't be shown. Lost it? Run:  sagex trigger regenerate …")


def describe_cron(expr: str) -> str | None:
    """Human-readable description of a cron expression (e.g. 'At 09:00, on day 1 …').

    Best-effort: returns None if the expression is empty, unparseable, or the
    cron_descriptor library isn't available — callers just omit the line.
    """
    if not expr:
        return None
    try:
        from cron_descriptor import get_description
        return get_description(expr)
    except Exception:
        return None


def schedule_trigger_result(data: dict) -> None:
    """Summary after creating/updating a schedule trigger."""
    console.print(Text("✓ Schedule trigger ready.", style="green"))
    body = Text()
    body.append(_kv("node", data.get("node_id")));            body.append("\n")
    body.append(_kv("cron", data.get("cron_expression")));    body.append("\n")
    desc = describe_cron(data.get("cron_expression"))
    if desc:
        body.append(_kv("when", f"{desc} (UTC)"));            body.append("\n")
    body.append(_kv("timezone", data.get("timezone")));       body.append("\n")
    active = data.get("is_active")
    st = Text(); st.append("active: ", style="bright_black")
    st.append("● yes", style="green") if active else st.append("○ no", style="bright_black")
    body.append(st)
    console.print(body)
    note("Cron is 5 fields (min hour day-of-month month day-of-week), evaluated in UTC.")


# --- credential (key) ------------------------------------------------------

_SECRET_FIELDS = ["username", "password", "ssh_key", "key_passphrase", "cert_pem"]


def credential(c: dict, secrets: dict | None) -> None:
    """Show a credential. Secrets are shown ONLY when `secrets` is provided."""
    header = Text()
    header.append(_kv("id", c.get("id")));                    header.append("\n")
    header.append(_kv("type", c.get("credential_type")));     header.append("\n")
    header.append(_kv("vault", c.get("vault_name") or c.get("vault")));   header.append("\n")
    header.append(_kv("created", c.get("created_at")));       header.append("\n")
    header.append(_kv("modified", c.get("modified_at")))
    console.print(Panel(header, title=c.get("name") or "(credential)", title_align="left"))

    if secrets is None:
        note("Secrets hidden — pass --reveal to display them (asks first).")
        return

    console.print(Text("⚠ Revealed secrets — now visible in your terminal/scrollback:", style="bold red"))
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column("field", style="bright_black", no_wrap=True)
    table.add_column("value")
    shown = False
    for field in _SECRET_FIELDS:
        value = secrets.get(field)
        if value:
            table.add_row(field, Text(str(value)))
            shown = True
    console.print(table) if shown else note("(no secret values set)")


# --- server ----------------------------------------------------------------

def server(s: dict) -> None:
    """Show a vault server, incl. its linked credential name and vault name."""
    host = s.get("host") or "—"
    port = s.get("port")
    cred = s.get("credential_details") if isinstance(s.get("credential_details"), dict) else {}

    header = Text()
    header.append(_kv("id", s.get("id")));                             header.append("\n")
    header.append(_kv("host", f"{host}:{port}" if port else host));    header.append("\n")
    header.append(_kv("method", s.get("connection_method")));          header.append("\n")
    header.append(_kv("credential", cred.get("name") or "—"));         header.append("\n")
    header.append(_kv("vault", s.get("vault_name") or s.get("vault")));header.append("\n")
    header.append(_kv("created", s.get("created_at")));                header.append("\n")
    header.append(_kv("modified", s.get("modified_at")))
    console.print(Panel(header, title=s.get("name") or "(server)", title_align="left"))


# --- vault -----------------------------------------------------------------

def vault(v: dict) -> None:
    """Show a vault: header panel + tables of its credentials and servers."""
    creds = v.get("credentials") or []
    servers = v.get("servers") or []

    header = Text()
    header.append(_kv("id", v.get("id")));                          header.append("\n")
    header.append(_kv("description", v.get("description") or "—")); header.append("\n")
    header.append(_kv("credentials", len(creds)));                  header.append("\n")
    header.append(_kv("servers", len(servers)));                    header.append("\n")
    header.append(_kv("created", v.get("created_at")));             header.append("\n")
    header.append(_kv("modified", v.get("modified_at")))
    console.print(Panel(header, title=v.get("name") or "(vault)", title_align="left"))

    if creds:
        table = Table(title="Credentials", show_header=True, header_style="bold", title_justify="left")
        table.add_column("name")
        table.add_column("type")
        for c in creds:
            table.add_row(str(c.get("name") or ""), str(c.get("credential_type") or ""))
        console.print(table)

    if servers:
        table = Table(title="Servers", show_header=True, header_style="bold", title_justify="left")
        table.add_column("name")
        table.add_column("host")
        table.add_column("method")
        for s in servers:
            host = s.get("host") or ""
            port = s.get("port")
            table.add_row(str(s.get("name") or ""), f"{host}:{port}" if port else str(host),
                          str(s.get("connection_method") or ""))
        console.print(table)


# ---------------------------------------------------------------------------
# JSON slimming — strip internal/noisy fields before printing --json output.
# ---------------------------------------------------------------------------

_SLIM: dict[str, list[str]] = {
    "workflow":   ["id", "name", "description", "modified_at", "created_at"],
    "script":     ["id", "name", "content_type", "version", "file_size", "updated_at"],
    "run":        ["id", "workflow_name", "status", "created_at", "started_at", "finished_at", "error_message"],
    "trigger":    ["workflow_name", "kind", "is_active", "node_id", "workflow_id",
                   "cron_expression", "timezone", "trigger_url", "secret_last4"],
    "credential": ["id", "name", "credential_type", "vault_name", "created_at", "modified_at"],
    "server":     ["id", "name", "host", "port", "connection_method", "vault_name", "created_at", "modified_at"],
}


def slim_items(items: list[dict], kind: str) -> list[dict]:
    """Return a copy of `items` with only the display-relevant fields for `kind`."""
    fields = _SLIM[kind]
    return [{k: it[k] for k in fields if k in it} for it in items]


def slim_vaults(items: list[dict]) -> list[dict]:
    """Vault-specific slim: top-level fields + compact nested cred/server lists."""
    out = []
    for v in items:
        d = {k: v[k] for k in ["id", "name", "description", "created_at", "modified_at"] if k in v}
        d["credentials"] = [
            {"id": c.get("id"), "name": c.get("name"), "type": c.get("credential_type")}
            for c in (v.get("credentials") or [])
        ]
        d["servers"] = [
            {"id": s.get("id"), "name": s.get("name"), "host": s.get("host"), "method": s.get("connection_method")}
            for s in (v.get("servers") or [])
        ]
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# List renderers — one per resource type, for the `sagex list` commands.
# ---------------------------------------------------------------------------


def list_workflows(items: list[dict]) -> None:
    if not items:
        note("No workflows found.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("modified", style="bright_black")
    for w in items:
        table.add_row(
            w.get("name") or "(unnamed)",
            str(w.get("id") or ""),
            relative_time(w.get("modified_at")),
        )
    console.print(table)


def list_scripts(items: list[dict]) -> None:
    if not items:
        note("No scripts found.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("type", style="bright_black")
    table.add_column("ver", style="bright_black")
    for s in items:
        ver = s.get("version")
        table.add_row(
            s.get("name") or "(unnamed)",
            str(s.get("id") or ""),
            s.get("content_type") or "—",
            f"v{ver}" if ver is not None else "—",
        )
    console.print(table)


def list_runs(items: list[dict]) -> None:
    if not items:
        note("No runs found.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("workflow")
    table.add_column("status")
    table.add_column("created", style="bright_black")
    for r in items:
        status = r.get("status") or "unknown"
        icon, color = STATUS_ICON.get(status, ("•", "white"))
        table.add_row(
            str(r.get("id") or "")[:8],
            r.get("workflow_name") or "—",
            Text(f"{icon} {status}", style=color),
            relative_time(r.get("created_at")),
        )
    console.print(table)


def list_triggers(items: list[dict]) -> None:
    if not items:
        note("No triggers found.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("workflow")
    table.add_column("kind")
    table.add_column("active")
    for t in items:
        is_active = t.get("is_active")
        active_text = Text("● yes", style="green") if is_active else Text("○ no", style="bright_black")
        table.add_row(
            t.get("workflow_name") or "—",
            t.get("kind") or "—",
            active_text,
        )
    console.print(table)


def list_credentials(items: list[dict]) -> None:
    if not items:
        note("No credentials found.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("type", style="bright_black")
    table.add_column("vault", style="bright_black")
    for c in items:
        table.add_row(
            c.get("name") or "(unnamed)",
            str(c.get("id") or ""),
            c.get("credential_type") or "—",
            c.get("vault_name") or str(c.get("vault") or "—"),
        )
    console.print(table)


def list_servers(items: list[dict]) -> None:
    if not items:
        note("No servers found.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("host")
    table.add_column("method", style="bright_black")
    for s in items:
        host = s.get("host") or "—"
        port = s.get("port")
        table.add_row(
            s.get("name") or "(unnamed)",
            f"{host}:{port}" if port else host,
            s.get("connection_method") or "—",
        )
    console.print(table)


def list_vaults(items: list[dict]) -> None:
    if not items:
        note("No vaults found.")
        return
    table = Table(show_header=True, header_style="bold")
    table.add_column("name")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("#creds", justify="right", style="bright_black")
    table.add_column("#servers", justify="right", style="bright_black")
    for v in items:
        creds = v.get("credentials") or []
        svrs = v.get("servers") or []
        table.add_row(
            v.get("name") or "(unnamed)",
            str(v.get("id") or ""),
            str(len(creds)) if isinstance(creds, list) else "—",
            str(len(svrs)) if isinstance(svrs, list) else "—",
        )
    console.print(table)
