"""The sagex terminal application: layout and event wiring.

Appearance lives in app.tcss; mock data in data.py; status formatting in
formatting.py. This file focuses on WHAT widgets exist and HOW they react.
"""

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Tree, Input

from sagex import config, shell
from sagex.api import ApiError, build_client, resources
from sagex.formatting import run_label
from sagex.widgets.message import ChatMessage
from sagex.widgets.prompt_input import PromptInput


class SagexApp(App):
    """The whole application. Everything hangs off this one class."""

    CSS_PATH = "app.tcss"                 # styling loaded from the file next to this one

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f2", "cycle_shell", "Shell"),
        ("escape", "stop_command", "Stop"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = config.load()                       # persisted settings (~/.sagex)
        self.session = shell.ShellSession(preferred=self.config.get("shell"))
        self._busy = False                                # is a command currently running?

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

        # Build categories with a "Loading…" placeholder, then fetch each from the
        # real API off the main thread (see _load_branch). Runs get status icons.
        workflows = tree.root.add("Workflows")
        workflows.add_leaf("Loading…")
        scripts = tree.root.add("Scripts")
        scripts.add_leaf("Loading…")
        vault = tree.root.add("Vault")
        vault.add_leaf("Loading…")
        runs = tree.root.add("Runs")
        runs.add_leaf("Loading…")
        tree.root.add("Triggers")
        tree.root.expand_all()           # start with everything opened up

        self._refresh_prompt()           # show "shell · cwd" on the input border

        # Kick off the loaders — they run concurrently in background threads.
        self._load_branch(workflows, resources.list_workflows)
        self._load_branch(scripts, resources.list_scripts)
        self._load_branch(vault, resources.list_vault_resources)
        self._load_branch(runs, lambda c: [run_label(*r) for r in resources.list_recent_runs(c)])

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
            if self._busy:                              # one command at a time
                self.add_message(
                    "⚠ A command is already running — press Esc to stop it first.",
                    role="autobot",
                )
                return
            self._busy = True
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
        self.call_from_thread(self._finish_command)   # clear busy flag + refresh prompt

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

    def action_stop_command(self) -> None:
        """Esc: stop the currently running command, if any."""
        self.session.cancel()            # kills the process; the worker then finishes

    def _finish_command(self) -> None:
        """(main thread) Called when a command finishes — clear the busy flag."""
        self._busy = False
        self._refresh_prompt()           # the working dir may have changed

    def action_cycle_shell(self) -> None:
        """F2: switch to the next installed shell (and remember the choice)."""
        self.session.cycle_shell()
        self.config["shell"] = self.session.shell        # persist across launches
        config.save(self.config)
        self._refresh_prompt()

    def _refresh_prompt(self) -> None:
        """Show 'shell · cwd' on the input box's top border."""
        self.query_one("#chat-input", Input).border_title = self.session.prompt

    @work(thread=True)
    def _load_branch(self, node, fetch) -> None:
        """Fetch display labels for a tree branch in a background thread.

        `fetch` takes an ApiClient and returns a list of labels (str or Text).
        """
        try:
            labels = fetch(build_client())
        except ApiError as exc:
            self.call_from_thread(self._fill_branch, node, None, exc.message)
            return
        self.call_from_thread(self._fill_branch, node, labels, None)

    def _fill_branch(self, node, labels, error) -> None:
        """(main thread) Replace a branch's contents with results, or an error line."""
        node.remove_children()           # clear the "Loading…" placeholder
        if error:
            node.add_leaf(f"⚠ {error}")
        elif not labels:
            node.add_leaf("(none)")
        else:
            for label in labels:
                node.add_leaf(label)
