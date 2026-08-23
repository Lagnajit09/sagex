"""The sagex terminal application: layout and event wiring.

Appearance lives in app.tcss; mock data in data.py; status formatting in
formatting.py. This file focuses on WHAT widgets exist and HOW they react.
"""

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Tree, Input

from sagex import config, data, shell
from sagex.formatting import run_label
from sagex.widgets.message import ChatMessage
from sagex.widgets.prompt_input import PromptInput


class SagexApp(App):
    """The whole application. Everything hangs off this one class."""

    CSS_PATH = "app.tcss"                 # styling loaded from the file next to this one

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f2", "cycle_shell", "Shell"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = config.load()                       # persisted settings (~/.sagex)
        self.session = shell.ShellSession(preferred=self.config.get("shell"))

    def compose(self) -> ComposeResult:
        """Declare WHAT is on screen, top to bottom."""
        yield Header(show_clock=True)

        # Horizontal split: navigable tree on the left, Autobot panel on the right.
        with Horizontal():
            yield Tree("Resources", id="resources")

            # The right side is a vertical stack: scrolling messages + input box.
            with Vertical(id="autobot"):
                yield VerticalScroll(id="chat-log")     # scrolls; holds ChatMessage widgets
                yield PromptInput(
                    placeholder="Ask Autobot…   (start with  !  to run a shell command)",
                    id="chat-input",
                )

        yield Footer()

    def on_mount(self) -> None:
        """Runs once, right after the widgets exist. Good place for setup."""
        self.query_one("#autobot", Vertical).border_title = "Autobot"

        tree = self.query_one("#resources", Tree)
        tree.border_title = "Resources"
        tree.show_root = False           # show categories at the top level

        # Build each category from the mock data in data.py.
        workflows = tree.root.add("Workflows")
        for name in data.WORKFLOWS:
            workflows.add_leaf(name)

        scripts = tree.root.add("Scripts")
        for name in data.SCRIPTS:
            scripts.add_leaf(name)

        vault = tree.root.add("Vault")
        for name in data.VAULT:
            vault.add_leaf(name)

        # Runs is the only category with status icons — a run has a real outcome.
        runs = tree.root.add("Runs")
        for status, name, when in data.RECENT_RUNS:
            runs.add_leaf(run_label(status, name, when))

        tree.root.add("Triggers")
        tree.root.expand_all()           # start with everything opened up

        self._refresh_prompt()           # show "shell · cwd" on the input border

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        """Fires whenever the highlight lands on a tree node (arrows or mouse)."""
        node = event.node
        chat_input = self.query_one("#chat-input", Input)

        # A branch (category) CAN be expanded; a leaf (real item) CANNOT. We show
        # the current selection as a small "context" chip on the input's border.
        if node.allow_expand:
            chat_input.border_subtitle = ""                              # category -> no context
        else:
            chat_input.border_subtitle = f"context: {str(node.label)}"   # real item -> show it

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Fires when the user presses Enter in the input box."""
        text = event.value.strip()
        event.input.value = ""           # clear the box either way
        if not text:                     # ignore empty / whitespace-only sends
            return
        self.query_one("#chat-input", PromptInput).add_to_history(text)   # for ↑/↓ recall

        if text.startswith("!"):         # "!" prefix -> run as a shell command
            command = text[1:].strip()
            if not command:
                return
            if command.lower() in ("clear", "cls"):     # UI builtin: wipe the chat log
                self.query_one("#chat-log", VerticalScroll).remove_children()
                return
            self.execute_command(command)
        else:                            # otherwise -> agent prompt (placeholder for now)
            self.add_message(text, role="user")
            self.add_message(
                "(placeholder reply — I'm not connected to the server yet.)",
                role="autobot",
            )

    @work(thread=True)
    def execute_command(self, command: str) -> None:
        """Run a shell command in a BACKGROUND THREAD, streaming its output live.

        This runs off the main thread, so every UI touch goes through
        `call_from_thread`, which hands the work back to Textual's main loop.
        """
        self.call_from_thread(self.add_message, command, "command")

        # Create the (empty) output block now, then fill it as lines arrive.
        out = self.call_from_thread(self._new_output_message)

        got_output = False

        def on_line(line: str) -> None:
            nonlocal got_output
            got_output = True
            self.call_from_thread(self._append_output, out, line)

        try:
            code = self.session.run_streaming(command, on_line)
        except Exception as exc:         # e.g. the shell itself couldn't start
            self.call_from_thread(self._append_output, out, f"Failed to run command: {exc}")
            code = -1

        if not got_output:
            self.call_from_thread(self._append_output, out, "(no output)")
        self.call_from_thread(self._append_output, out, f"[exit code: {code}]")
        self.call_from_thread(self._refresh_prompt)   # the working dir may have changed

    def _new_output_message(self) -> ChatMessage:
        """(main thread) Mount an empty cmd_output block and return it."""
        widget = ChatMessage("", "cmd_output")
        self.query_one("#chat-log", VerticalScroll).mount(widget)
        return widget

    def _append_output(self, widget: ChatMessage, line: str) -> None:
        """(main thread) Append a line to an output block and scroll to it."""
        widget.append_line(line)
        self.query_one("#chat-log", VerticalScroll).scroll_end()

    def add_message(self, text: str, role: str) -> None:
        """Append a chat message to the log and scroll it into view."""
        log = self.query_one("#chat-log", VerticalScroll)
        log.mount(ChatMessage(text, role))
        self.call_after_refresh(log.scroll_end)   # scroll after the new widget lays out

    def action_cycle_shell(self) -> None:
        """F2: switch to the next installed shell (and remember the choice)."""
        self.session.cycle_shell()
        self.config["shell"] = self.session.shell        # persist across launches
        config.save(self.config)
        self._refresh_prompt()

    def _refresh_prompt(self) -> None:
        """Show 'shell · cwd' on the input box's top border."""
        self.query_one("#chat-input", Input).border_title = self.session.prompt
