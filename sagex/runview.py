"""Live state of a workflow run, drawn as a compact graph that updates as it streams.

Shared by the CLI (a pinned Rich Live region) and the TUI (a widget fed by event
lines the CLI prints when SAGEX_EVENTS=1), so both show the same picture. Every
change goes through `apply(event)`:

    {"t": "node", "id": ..., "status": ..., "exit_code": ..., "duration": ..., "branch": ...,
     "elapsed": <seconds a running node has already run, optional>}
    {"t": "run", "status": ...}

The TUI first receives {"t": "start", "view": <snapshot()>} to build its copy.
"""

import json
import time

from rich.cells import cell_len
from rich.panel import Panel
from rich.text import Text

from sagex.formatting import ACCENT, STATUS_ICON, pulse_glyph

EVENT_PREFIX = "::sagex-run:: "

_FINISHED = ("success", "failed", "cancelled")
_KIND_ICON = {"trigger": "▸", "decision": "◆", "email": "■", "script": "●"}
_RESULT_COLOR = {"success": "green", "failed": "red", "cancelled": "bright_black"}


def event_line(event: dict) -> str:
    """Encode an event as one line for the TUI to pick out of a command's output."""
    return EVENT_PREFIX + json.dumps(event, separators=(",", ":"))


def parse_event_line(line: str) -> dict | None:
    """The event carried by `line`, or None if it's ordinary output."""
    if not line.startswith(EVENT_PREFIX):
        return None
    try:
        event = json.loads(line[len(EVENT_PREFIX):])
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _elapsed(seconds: float) -> str:
    """'2.3s', '4m 05s', '7h 10m' — a finished node's duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    return _clock(seconds)


def _clock(seconds: float) -> str:
    """'12s', '4m 05s', '7h 10m' — whole seconds, for a node that's still running."""
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


class WorkflowRunView:
    """A run's per-node state plus the graph shape needed to draw it."""

    def __init__(self, name: str, nodes: list[dict], edges: list[list]) -> None:
        self.name = name
        self.order = [n["id"] for n in nodes]
        self.nodes = {n["id"]: {"label": n.get("label") or n["id"], "kind": n.get("kind") or ""}
                      for n in nodes}
        self.edges = [list(e) for e in edges]
        self.children: dict = {}
        indeg = {nid: 0 for nid in self.order}
        for source, target, handle in self.edges:
            self.children.setdefault(source, []).append((target, handle))
            if target in indeg:
                indeg[target] += 1
        triggers = [nid for nid in self.order if self.nodes[nid]["kind"] == "trigger"]
        self.roots = triggers or [nid for nid, d in indeg.items() if d == 0] or self.order[:1]
        self.state = {nid: {"status": "pending"} for nid in self.order}
        self.status = "queued"

    @classmethod
    def from_workflow(cls, wf: dict) -> "WorkflowRunView":
        """Build from a workflow record (its nodes + edges)."""
        nodes = []
        for n in wf.get("nodes") or []:
            data = n.get("data") if isinstance(n.get("data"), dict) else {}
            kind = n.get("type") or ""
            if kind == "action":
                kind = data.get("type") or "action"
            nodes.append({"id": n.get("id"), "label": data.get("label") or n.get("id"), "kind": kind})
        edges = [[e.get("source"), e.get("target"), e.get("sourceHandle") or None]
                 for e in wf.get("edges") or []]
        return cls(wf.get("name") or "(workflow)", nodes, edges)

    def snapshot(self) -> dict:
        """The graph shape, for sending to the TUI."""
        return {"name": self.name,
                "nodes": [{"id": nid, **self.nodes[nid]} for nid in self.order],
                "edges": self.edges}

    @classmethod
    def from_snapshot(cls, snap: dict) -> "WorkflowRunView":
        return cls(snap.get("name") or "(workflow)", snap.get("nodes") or [], snap.get("edges") or [])

    @property
    def finished(self) -> bool:
        return self.status in _FINISHED

    def label(self, node_id) -> str:
        return (self.nodes.get(node_id) or {}).get("label") or str(node_id)

    def kind(self, node_id) -> str:
        return (self.nodes.get(node_id) or {}).get("kind") or ""

    # --- updates -------------------------------------------------------------

    def apply(self, event: dict) -> None:
        """Apply one {"t": "node" | "run", ...} event."""
        if event.get("t") == "node":
            st = self.state.get(event.get("id"))
            if st is None:
                return
            status = event.get("status") or st["status"]
            if status == "running" and (st["status"] != "running" or event.get("elapsed") is not None):
                # `elapsed` (seconds already spent running) comes from polled records.
                st["since"] = time.monotonic() - max(float(event.get("elapsed") or 0), 0.0)
            st["status"] = status
            for key in ("exit_code", "duration", "branch"):
                if event.get(key) is not None:
                    st[key] = event[key]
            if self.status == "queued":
                self.status = "running"
            self._settle_triggers()
        elif event.get("t") == "run":
            self.status = event.get("status") or self.status
            if self.status != "queued":
                self._settle_triggers()

    def _settle_triggers(self) -> None:
        # The trigger finishes the instant a run starts and usually streams before
        # we connect, so it's marked done as soon as the run is under way.
        for nid in self.roots:
            if self.kind(nid) == "trigger" and self.state[nid]["status"] == "pending":
                self.state[nid]["status"] = "success"

    # --- drawing -------------------------------------------------------------

    def __rich__(self) -> Panel:
        return self.panel()

    def panel(self) -> Panel:
        """The graph in a full-width border: dim and titled 'live' while running,
        coloured by the result once the run has finished."""
        if self.finished:
            color = _RESULT_COLOR.get(self.status, "bright_black")
            title = Text.assemble((self.name, "bold"), (f" · {self.status}", color))
        else:
            color = "bright_black"
            title = Text.assemble((self.name, "bold"), (" · live", "bright_black"))
        return Panel(self.graph(), title=title, title_align="left", border_style=color,
                     padding=(0, 1), expand=True)

    def graph(self) -> Text:
        """One line per node (linear steps stack, a decision fans out to its branches),
        with each node's details lined up in one column."""
        visited: set = set()
        rows: list[tuple[Text, int, str]] = []
        for root in self.roots:
            if root in self.nodes and root not in visited:
                rows += self._block(root, visited)
        for nid in self.order:
            if nid not in visited:
                rows += self._block(nid, visited)
        column = max((width for _, width, details in rows if details), default=0) + 3
        lines = []
        for head, width, details in rows:
            line = head.copy()
            if details:
                line.append(" " * (column - width) + details, style="bright_black")
            lines.append(line)
        return Text("\n").join(lines)

    def _block(self, node_id, visited: set) -> list[tuple[Text, int, str]]:
        """(head, head width in cells, details) rows for the subtree at node_id."""
        visited.add(node_id)
        rows = [self._row(node_id)]
        kids = self.children.get(node_id, [])
        if len(kids) == 1 and self.kind(node_id) != "decision":
            return rows + self._child(kids[0][0], visited)
        width = max((len(h or "") for _, h in kids), default=0)
        for i, (target, handle) in enumerate(kids):
            last = i == len(kids) - 1
            tag = f"{(handle or '').ljust(width)} " if width else ""
            first = ("└─ " if last else "├─ ") + tag
            rest = ("   " if last else "│  ") + " " * len(tag)
            for j, (head, head_width, details) in enumerate(self._child(target, visited)):
                prefix = first if j == 0 else rest
                row = Text(prefix, style="bright_black")
                row.append(head)
                rows.append((row, len(prefix) + head_width, details))
        return rows

    def _child(self, target, visited: set) -> list[tuple[Text, int, str]]:
        if target not in self.nodes:
            note = f"? {target} (missing node)"
            return [(Text(note, style="red"), cell_len(note), "")]
        if target in visited:
            note = f"↑ {self.label(target)} (above)"
            return [(Text(note, style="bright_black"), cell_len(note), "")]
        return self._block(target, visited)

    def _row(self, node_id) -> tuple[Text, int, str]:
        st = self.state[node_id]
        status = st["status"]
        label = self.label(node_id)
        if len(label) > 40:
            label = label[:39] + "…"
        details: list[str] = []
        head = Text()
        if status == "running":
            since = time.monotonic() - st.get("since", time.monotonic())
            head.append(f"{pulse_glyph(since)} ", style=ACCENT)
            head.append(label, style="bold")
            details.append(f"running {_clock(since)}")
        elif status == "pending":
            head.append(f"{_KIND_ICON.get(self.kind(node_id), '•')} ", style="bright_black")
            head.append(label, style="bright_black")
            if self.finished:
                details.append("not run")
        else:
            icon, color = STATUS_ICON.get(status, ("•", "white"))
            muted = status in ("skipped", "cancelled")
            head.append(f"{icon} ", style=color)
            head.append(label, style="red" if status == "failed" else ("bright_black" if muted else ""))
            if muted:
                details.append(status)
            if st.get("branch"):
                details.append(f"→ {st['branch']}")
            if st.get("exit_code") not in (None, -1):
                details.append(f"exit {st['exit_code']}")
            if st.get("duration") and not muted:
                details.append(_elapsed(float(st["duration"])))
        # Icon glyphs are counted as one cell: terminals draw them that wide even
        # where Rich's width table says two.
        return head, 2 + cell_len(label), " · ".join(details)
