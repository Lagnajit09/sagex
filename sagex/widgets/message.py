"""ChatMessage — a message block in the Autobot chat log.

Supports four kinds of message ("role"), each styled uniquely via a CSS class:
  - "user"       : what you typed to Autobot          (accent bar, no label)
  - "autobot"    : Autobot's reply                     ("Autobot" label)
  - "command"    : a native shell command you ran      (no label, "❯" prompt)
  - "cmd_output" : the output of that command          ("Output" label, muted)

Output blocks grow line-by-line as a command streams (see append_line).
"""

from rich.text import Text

from textual.widgets import Static


# Roles that show a bold label above their text. Roles not listed show no label.
ROLE_LABELS = {
    "autobot": "Autobot",
    "cmd_output": "Output",
}


class ChatMessage(Static):
    """One chat message. `role` is one of the keys used below."""

    DEFAULT_CSS = """
    ChatMessage {
        width: 1fr;                          /* span the chat log width */
        height: auto;                        /* grow to fit the text */
        padding: 0 1;
        margin-top: 1;                       /* gap above each message */
        border-left: thick $primary;         /* default look = Autobot */
    }
    ChatMessage.user {
        border-left: thick $accent;
    }
    ChatMessage.command {
        border-left: thick $success;         /* command = bright accent + prompt */
    }
    ChatMessage.cmd_output {
        border-left: thick $success-darken-2;
        color: $text-muted;                  /* output recedes visually */
    }
    """

    def __init__(self, text: str, role: str) -> None:
        super().__init__()
        self._role = role
        self._body = text
        self.add_class(role)                 # CSS class: user/autobot/command/cmd_output
        self._rebuild()

    def _rebuild(self) -> None:
        """Rebuild the displayed content from the current body text."""
        content = Text()
        if self._role == "command":
            content.append("❯ ", style="bold")       # shell-style prompt, no label
        else:
            label = ROLE_LABELS.get(self._role)      # None for "user"
            if label:
                content.append(f"{label}\n", style="bold")
        content.append(self._body)
        self.update(content)                          # swap in the new renderable

    def append_line(self, line: str) -> None:
        """Add one line to the body (used for streaming command output)."""
        self._body = f"{self._body}\n{line}" if self._body else line
        self._rebuild()
