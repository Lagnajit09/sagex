"""The sagex terminal application: layout and event wiring.

Appearance lives in app.tcss; mock data in data.py; status formatting in
formatting.py. This file focuses on WHAT widgets exist and HOW they react.
"""

import os

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Tree, Input, Static, DirectoryTree

from sagex import config, shell
from sagex.api import ApiError, build_client, resources, store
from sagex.formatting import run_label, trigger_label
from sagex.widgets.message import ChatMessage
from sagex.widgets.prompt_input import PromptInput


class SagexApp(App):
    """The whole application. Everything hangs off this one class."""

    CSS_PATH = "app.tcss"                 # styling loaded from the file next to this one

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f2", "cycle_shell", "Shell"),
        ("f3", "toggle_env", "Environment"),
        ("f5", "refresh", "Refresh"),
        ("escape", "stop_command", "Stop"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.config = config.load()                       # persisted settings (~/.sagex)
        self.session = shell.ShellSession(preferred=self.config.get("shell"))
        self._busy = False                                # is a command currently running?

        # Workspace = the local folder shown in the "local" environment.
        ws = self.config.get("workspace")
        self._workspace = ws if ws and os.path.isdir(ws) else os.getcwd()
        # Start in the server environment if a key is stored, else local-only.
        self._env = "server" if store.get_key() else "local"

    def compose(self) -> ComposeResult:
        """Declare WHAT is on screen, top to bottom."""
        yield Header(show_clock=True)

        # Horizontal split: resources on the left, Autobot panel on the right.
        with Horizontal():
            # Left: the tree, plus a one-line status bar docked at its bottom.
            with Vertical(id="resources-panel"):
                yield Input(                                               # local: set the folder
                    value=self._workspace,
                    placeholder="workspace path (relative or absolute)",
                    id="workspace-input",
                )
                yield Tree("Resources", id="resources")                    # server environment
                yield DirectoryTree(self._workspace, id="workspace-tree")  # local environment
                yield Static(id="resources-status")

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
        tree.show_root = False           # show categories at the top level

        # Build the categories once; a loader (re)fills each. Runs get status icons.
        workflows = tree.root.add("Workflows")
        scripts = tree.root.add("Scripts")
        vault = tree.root.add("Vault")
        runs = tree.root.add("Runs")
        triggers = tree.root.add("Triggers")
        tree.root.expand_all()

        # (branch node, fetch function) pairs — reused by refresh.
        self._loaders = [
            (workflows, resources.list_workflows),
            (scripts, resources.list_scripts),
            (vault, resources.list_vault_resources),
            (runs, lambda c: [run_label(*r) for r in resources.list_recent_runs(c)]),
            (triggers, lambda c: [trigger_label(*t) for t in resources.list_triggers(c)]),
        ]

        self._refresh_prompt()           # show "shell · cwd" on the input border
        self._apply_env()                # show the right tree + status for the current env
        if self._env == "server":
            self._start_loaders()        # fetch server resources (concurrently, off-thread)

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
        """Fires when the user presses Enter in an input box."""
        if event.input.id == "workspace-input":     # the local-env path box
            self._set_workspace(event.value)
            return

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

    def _start_loaders(self) -> None:
        """(Re)load every resource branch, resetting each to 'Loading…' first."""
        self._set_status("")             # clear any previous error
        for node, fetch in self._loaders:
            node.remove_children()
            node.add_leaf("Loading…")
            self._load_branch(node, fetch)

    def action_refresh(self) -> None:
        """F5: reload the current environment (also the retry for a failed load)."""
        if self._env == "server":
            self._start_loaders()
        else:
            self.query_one("#workspace-tree", DirectoryTree).reload()

    def action_toggle_env(self) -> None:
        """F3: switch between the server and local environments (only when logged in)."""
        if not store.get_key():
            return                       # no toggle when not authenticated
        self._env = "local" if self._env == "server" else "server"
        self._apply_env()
        if self._env == "server":
            self._start_loaders()

    def check_action(self, action: str, parameters):
        """Hide the environment toggle from the footer when not logged in."""
        if action == "toggle_env":
            return True if store.get_key() else None   # None -> hidden + disabled
        return True

    def _apply_env(self) -> None:
        """Show the tree for the current environment; update the panel title + status."""
        server = self._env == "server"
        self.query_one("#resources", Tree).display = server
        self.query_one("#workspace-tree", DirectoryTree).display = not server
        self.query_one("#workspace-input", Input).display = not server
        self.query_one("#resources-panel", Vertical).border_title = (
            "Resources" if server else "Workspace"
        )
        self._update_env_status()

    def _set_workspace(self, path: str) -> None:
        """Validate + persist a new workspace path, then reload the local tree."""
        try:
            resolved = config.resolve_workspace(path)
        except ValueError as exc:
            self._set_status(f"⚠ {exc}")
            return
        self._workspace = resolved
        self.config["workspace"] = resolved
        config.save(self.config)
        self.query_one("#workspace-input", Input).value = resolved
        self.query_one("#workspace-tree", DirectoryTree).path = resolved   # reactive -> reloads
        self._update_env_status()        # refresh "local · <path>"

    def _update_env_status(self) -> None:
        """Set the bottom status line based on auth state and current environment."""
        if not store.get_key():
            self._set_status("○ Not authenticated · python -m sagex auth login")
        elif self._env == "local":
            self._set_status(f"local · {self._workspace}")
        else:
            self._set_status("")         # server ok; a load error will overwrite this

    def _set_status(self, text: str) -> None:
        """Set the one-line status bar under the resources tree."""
        self.query_one("#resources-status", Static).update(text)

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
        """(main thread) Fill a branch with results; route any error to one status line."""
        node.remove_children()           # clear the "Loading…" placeholder
        if error:
            self._set_status(f"⚠ {error}  ·  F5 to retry")   # one shared message, not per-branch
            return
        if not labels:
            node.add_leaf("(none)")
        else:
            for label in labels:
                node.add_leaf(label)   # names already trimmed at construction (metadata kept)
