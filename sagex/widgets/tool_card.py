"""ToolCard — one tool call by Sage, shown in the transcript.

    ╭─ sagex_read ───────────────────────────────────────────╮
    │ $ sagex show script deploy.sh                          │
    │ ────────────────────────────────────────────────────── │
    │ deploy.sh · python · v3                                │
    │ ▸ 38 more lines · click to expand                      │
    ╰───────────────────────── ✓ 0.4s · Sage saw all of it ─╯

The header says which tool Sage called, the first line the exact sagex command,
then its output — streaming while it runs, folded to its last lines when it
ends (click to expand). The footer says how it ended and how much of the output
Sage received. `sagex_help` cards start folded: it's Sage reading docs.
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from textual.widgets import Static

from sagex.formatting import ACCENT

LIVE_LINES = 15          # visible while running
FOLDED_LINES = 8         # visible once finished (until expanded)
MAX_LINES = 2000         # kept for expanding; older lines drop off


class ToolCard(Static):
    """A tool call's card. Update it from the main thread only."""

    DEFAULT_CSS = """
    ToolCard {
        width: 1fr;
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, tool: str, command: str, *, folded_lines: int = FOLDED_LINES) -> None:
        super().__init__()
        self.tool = tool
        self.command = command
        self.lines: list[str] = []
        self._dropped = 0
        self._folded_lines = folded_lines
        self._expanded = False
        self._state = "running"          # running | ok | failed | refused | stopped
        self._footer = "running…"
        self._note: str | None = None    # e.g. the refusal reason
        self._rebuild()

    @property
    def state(self) -> str:
        return self._state

    # --- updates ------------------------------------------------------------------

    def append_line(self, line: str) -> None:
        self.lines.append(line)
        if len(self.lines) > MAX_LINES:
            del self.lines[0]
            self._dropped += 1
        self._rebuild()

    def set_lines(self, lines: list[str]) -> None:
        self.lines = list(lines[-MAX_LINES:])
        self._dropped = max(0, len(lines) - MAX_LINES)
        self._rebuild()

    def finish(self, exit_code: int, seconds: float | None, *, sage_saw: str) -> None:
        self._state = "ok" if exit_code == 0 else "failed"
        mark = "✓" if exit_code == 0 else f"✗ exit {exit_code}"
        took = f" · {seconds:.1f}s" if seconds is not None else ""
        self._footer = f"{mark}{took} · {sage_saw}"
        self._rebuild()

    def refuse(self, reason: str) -> None:
        self._state = "refused"
        self._note = reason
        self._footer = "not run"
        self._rebuild()

    def stop(self, reason: str = "stopped") -> None:
        self._state = "stopped"
        self._footer = reason
        self._rebuild()

    def on_click(self) -> None:
        if self._state != "running" and len(self.lines) > self._folded_lines:
            self._expanded = not self._expanded
            self._rebuild()

    # --- rendering ----------------------------------------------------------------

    def _visible(self) -> tuple[list[str], int, bool]:
        """(lines to show, how many are hidden, whether the hidden ones come first).

        Live and failed output shows its end (where errors are); a successful
        command folds to its start (headers, the first rows).
        """
        if self._expanded and self._state != "running":
            return self.lines, self._dropped, True
        limit = LIVE_LINES if self._state == "running" else self._folded_lines
        from_end = self._state != "ok"
        if not limit:
            shown = []
        else:
            shown = self.lines[-limit:] if from_end else self.lines[:limit]
        return shown, len(self.lines) - len(shown) + self._dropped, from_end

    def _rebuild(self) -> None:
        color = {"running": ACCENT, "ok": "green", "failed": "red",
                 "refused": "yellow", "stopped": "bright_black"}[self._state]
        parts: list = [Text.assemble(("$ ", "bright_black"), (self.command, "bold"))]
        if self._note:
            parts.append(Text(self._note, style="yellow"))
        shown, hidden, hidden_first = self._visible()
        if shown or hidden:
            parts.append(Rule(style="bright_black"))
        foldable = self._state != "running" and len(self.lines) > self._folded_lines
        if self._expanded and foldable:
            label = Text("▾ click to collapse", style="bright_black")
        elif foldable:
            where = "earlier" if hidden_first else "more"
            label = Text(f"▸ {hidden} {where} lines · click to expand", style="bright_black")
        elif hidden:
            label = Text(f"… {hidden} earlier lines", style="bright_black")
        else:
            label = None
        if label is not None and hidden_first:
            parts.append(label)
        parts.extend(Text(line) for line in shown)
        if label is not None and not hidden_first:
            parts.append(label)
        self.update(Panel(
            Group(*parts),
            title=Text(self.tool, style=f"bold {color}"), title_align="left",
            subtitle=Text(self._footer, style=color), subtitle_align="right",
            border_style=color, padding=(0, 1),
        ))
