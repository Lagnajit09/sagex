"""Terminal rendering for the `sagex show` commands (Rich console output).

This is the CLI-side presentation layer: it takes the plain data returned by
`sagex.api.resources` and prints it nicely to stdout. The TUI will render the
same data as widgets later; only this file is CLI-specific.
"""

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

console = Console()


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
