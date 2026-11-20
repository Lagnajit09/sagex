"""ToolCard — one tool call by Sage, shown in the transcript.

    ╭─ sagex_read ───────────────────────────────────────────╮
    │ $ sagex show script deploy.sh                          │
    │ ────────────────────────────────────────────────────── │
    │ deploy.sh · python · v3                                │
    │ ▸ 38 more lines · click to expand                      │
    ╰───────────────────────── ✓ 0.4s · Sage saw all of it ─╯

The header says which tool Sage called, the first line the exact sagex command,
then its output — streaming while it runs, folded when it ends (click to
expand). The footer says how it ended and how much of the output Sage received.
`sagex_help` cards start folded: it's Sage reading docs.

A change (`sagex_change`) first asks:

    ╭─ sagex_change ─────────────────────────── needs your approval ─╮
    │ Run deploy-app with version=1.4                                 │  ← Sage's summary
    │ $ sagex run workflow deploy-app -p version=1.4                  │  ← what will run
    │ ─────────────────────────────────────────────────────────────── │
    │ y run it  ·  a allow `sagex run workflow deploy-app` this       │
    │ session  ·  n don't run                                         │
    ╰─────────────────────────────────────────────────────────────────╯

Sage's summary is its own words; the command line below it is what actually
runs, and a delete's warning comes from sagex, not from Sage. A workflow run
draws its live graph inside the card, above the output.

File and shell cards work the same way: `write_file` previews the new file
(or a coloured diff) above the choices, `read_file` shows what Sage would
receive, and `run_shell` shows the shell it runs in (`powershell ❯ …`).
"""

from __future__ import annotations

import asyncio
import time

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from textual import events
from textual.widgets import Static

from sagex.formatting import ACCENT
from sagex.runview import WorkflowRunView

LIVE_LINES = 15          # visible while running
FOLDED_LINES = 8         # visible once finished (until expanded)
MAX_LINES = 2000         # kept for expanding; older lines drop off
ARM_SECONDS = 0.4        # keys typed ahead before a card appears don't answer it

_COLORS = {"asking": "yellow", "running": ACCENT, "ok": "green", "failed": "red",
           "refused": "yellow", "declined": "bright_black", "stopped": "bright_black"}


class ToolCard(Static):
    """A tool call's card. Update it from the main thread only."""

    DEFAULT_CSS = """
    ToolCard {
        width: 1fr;
        height: auto;
        margin-top: 1;
    }
    """

    def __init__(self, tool: str, command: str, *, folded_lines: int = FOLDED_LINES,
                 summary: str | None = None, warning: str | None = None,
                 glyph: str = "$ ") -> None:
        super().__init__()
        self.tool = tool
        self.command = command
        self.summary = summary
        self.warning = warning
        self.glyph = glyph               # "$ " for commands, "▸ " for file operations
        self.diff = False                # lines are a unified diff (colour + and -)
        self.lines: list[str] = []
        self._dropped = 0
        self._folded_lines = folded_lines
        self._expanded = False
        self._state = "running"          # asking | running | ok | failed | refused | declined | stopped
        self._footer = "running…"
        self._note: str | None = None    # e.g. the refusal reason, "allowed for this session"
        self._choices: list[tuple[str, str]] = []
        self._answer: asyncio.Future | None = None
        self._armed_at = 0.0
        self._view: WorkflowRunView | None = None
        self._timer = None
        self._rebuild()

    @property
    def state(self) -> str:
        return self._state

    # --- approval -----------------------------------------------------------------

    def ask(self, choices: list[tuple[str, str]]) -> asyncio.Future:
        """Show the choices — (key, label) pairs — and return a future that
        resolves to the key pressed (or None if the question is withdrawn)."""
        self._state = "asking"
        self._footer = "needs your approval"
        self._choices = choices
        self._answer = asyncio.get_running_loop().create_future()
        self._armed_at = time.monotonic() + ARM_SECONDS
        self.can_focus = True
        self._rebuild()
        self.focus()
        return self._answer

    def withdraw(self) -> None:
        """Stop asking (Esc, or no answer in time); the future resolves to None."""
        self._settle(None)

    def approved(self, note: str | None = None) -> None:
        """The user said yes: it's running now."""
        self._state = "running"
        self._footer = "running…"
        self._note = note
        self._rebuild()

    def on_key(self, event: events.Key) -> None:
        if self._state != "asking":
            return
        key = (event.character or "").lower()
        if key in {k for k, _ in self._choices}:
            event.stop()
            if time.monotonic() >= self._armed_at:
                self._settle(key)

    def _settle(self, key: str | None) -> None:
        self._choices = []
        self.can_focus = False
        if self._answer is not None and not self._answer.done():
            self._answer.set_result(key)

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

    def run_event(self, event: dict) -> None:
        """A workflow run's progress: the first event starts the live graph."""
        if event.get("t") == "start":
            self._view = WorkflowRunView.from_snapshot(event.get("view") or {})
            if self._timer is None and self.is_attached:
                self._timer = self.set_interval(1 / 8, self._rebuild)   # running nodes pulse
        elif self._view is not None:
            self._view.apply(event)
            if self._view.finished:
                self._freeze()
        self._rebuild()

    def finish(self, exit_code: int, seconds: float | None, *, sage_saw: str) -> None:
        self._state = "ok" if exit_code == 0 else "failed"
        mark = "✓" if exit_code == 0 else f"✗ exit {exit_code}"
        took = f" · {seconds:.1f}s" if seconds is not None else ""
        self._footer = f"{mark}{took} · {sage_saw}"
        self._freeze()
        self._rebuild()

    def refuse(self, reason: str) -> None:
        self._state = "refused"
        self._note = reason
        self._footer = "not run"
        self._rebuild()

    def decline(self, reason: str) -> None:
        """The user said no (or didn't answer): nothing ran."""
        self._state = "declined"
        self._note = reason
        self._footer = "not run"
        self._rebuild()

    def stop(self, reason: str = "stopped") -> None:
        self._state = "stopped"
        self._footer = reason
        self._freeze()
        self._rebuild()

    def on_click(self) -> None:
        if self._state == "asking":
            self.focus()                 # e.g. after clicking elsewhere: keys answer it again
        if self._state != "running" and len(self.lines) > self._limit():
            self._expanded = not self._expanded
            self._rebuild()

    def _freeze(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # --- rendering ----------------------------------------------------------------

    def _limit(self) -> int:
        return LIVE_LINES if self._state in ("running", "asking") else self._folded_lines

    def _visible(self) -> tuple[list[str], int, bool]:
        """(lines to show, how many are hidden, whether the hidden ones come first).

        Live and failed output shows its end (where errors are); a successful
        command, and a preview waiting for approval (a file to write), fold to
        their start.
        """
        if self._expanded and self._state != "running":
            return self.lines, self._dropped, True
        limit = self._limit()
        from_end = self._state not in ("ok", "asking")
        if not limit:
            shown = []
        else:
            shown = self.lines[-limit:] if from_end else self.lines[:limit]
        return shown, len(self.lines) - len(shown) + self._dropped, from_end

    def _line_text(self, line: str) -> Text:
        """Output as plain text; a diff preview (an overwrite) gets its colours."""
        if self.diff and line and line[0] in "+-@":
            return Text(line, style={"+": "green", "-": "bold red", "@": "cyan"}[line[0]])
        return Text(line)

    def _choices_text(self) -> Text:
        """One choice per line, so a long one never splits a key from its label."""
        return Text("\n").join(Text.assemble((f" {key} ", "bold reverse"), f" {label}")
                               for key, label in self._choices)

    def _rebuild(self) -> None:
        color = _COLORS[self._state]
        parts: list = []
        if self.summary:
            parts.append(Text(self.summary, style="bold"))
        parts.append(Text.assemble((self.glyph, "bright_black"),
                                   (self.command, "" if self.summary else "bold")))
        if self.warning:
            parts.append(Text(f"⚠ {self.warning}", style="bold red"))
        if self._note:
            parts.append(Text(self._note, style="yellow" if self._state == "refused" else "bright_black"))
        if self._view is not None:
            parts += [Rule(style="bright_black"), self._view.graph()]
        shown, hidden, hidden_first = self._visible()
        if shown or hidden:
            parts.append(Rule(style="bright_black"))
        foldable = self._state != "running" and len(self.lines) > self._limit()
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
        parts.extend(self._line_text(line) for line in shown)
        if label is not None and not hidden_first:
            parts.append(label)
        if self._state == "asking":      # the choices last, right where the eye ends up
            parts += [Rule(style="bright_black"), self._choices_text()]
        self.update(Panel(
            Group(*parts),
            title=Text(self.tool, style=f"bold {color}"), title_align="left",
            subtitle=Text(self._footer, style=color), subtitle_align="right",
            border_style=color, padding=(0, 1),
        ))
