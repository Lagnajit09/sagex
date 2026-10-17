"""ChatMessage — one entry in the terminal-style transcript.

Entries read like a terminal session rather than chat bubbles. Each role has a
CSS class:
  - "user"       : a line you typed for Autobot         ❯ how do I …
  - "command"    : a shell command you ran (! prefix)    ❯ !ls
  - "cmd_output" : that command's output, indented below it
  - "autobot"    : Autobot's reply                       ● …

Output entries grow line by line as a command streams (see append_line).
"""

from rich.text import Text

from textual.widgets import Static

from sagex.formatting import ACCENT


class ChatMessage(Static):
    """One transcript entry. `role` is one of the keys above."""

    DEFAULT_CSS = """
    ChatMessage {
        width: 1fr;
        height: auto;
        padding: 0;
    }
    ChatMessage.user, ChatMessage.command, ChatMessage.autobot {
        margin-top: 1;                       /* a blank line before each new turn */
    }
    ChatMessage.cmd_output {
        padding-left: 2;                     /* output sits under its command */
        color: $text-muted;
    }
    """

    def __init__(self, text: str, role: str) -> None:
        super().__init__()
        self._role = role
        self._body = text
        self._exit_code: int | None = None
        self.add_class(role)
        self._rebuild()

    @property
    def is_empty(self) -> bool:
        return not self._body and self._exit_code in (None, 0)

    def _rebuild(self) -> None:
        content = Text()
        if self._role in ("user", "command"):
            content.append("❯ ", style=f"bold {ACCENT}")
            if self._role == "command":
                content.append("!", style="bright_black")
        elif self._role == "autobot":
            content.append("● ", style=ACCENT)
        content.append(self._body)
        if self._exit_code not in (None, 0):               # like a shell: only failures say so
            if self._body:
                content.append("\n")
            content.append(f"exit {self._exit_code}", style="red")
        self.update(content)

    def append_line(self, line: str) -> None:
        """Add one line to the body (used for streaming command output)."""
        self._body = f"{self._body}\n{line}" if self._body else line
        self._rebuild()

    def set_exit_code(self, code: int) -> None:
        """Record how the command ended (shown only when it failed)."""
        self._exit_code = code
        self._rebuild()
