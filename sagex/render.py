"""Terminal rendering for the `sagex show` commands (Rich console output).

This is the CLI-side presentation layer: it takes the plain data returned by
`sagex.api.resources` and prints it nicely to stdout. The TUI will render the
same data as widgets later; only this file is CLI-specific.
"""

from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from sagex.formatting import STATUS_ICON

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
        header.append("\n"); header.append(_kv("timezone", t.get("timezone")))
        if t.get("last_run_id"):
            header.append("\n"); header.append(_kv("last run", t.get("last_run_id")))
        if t.get("last_error"):
            header.append("\n")
            err = Text(); err.append("last error: ", style="bright_black"); err.append(str(t.get("last_error")), style="red")
            header.append(err)

    console.print(Panel(header, title=t.get("workflow_name") or "(trigger)", title_align="left"))


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
