"""The sagex terminal application: layout and event wiring.

Appearance lives in app.tcss; mock data in data.py; status formatting in
formatting.py. This file focuses on WHAT widgets exist and HOW they react.
"""

import os

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Tree, Input, Static, DirectoryTree

from sagex import config, runview, shell
from sagex.api import ApiError, build_client, resources, store
from sagex.formatting import ACCENT, run_label, trigger_label
from sagex.widgets.activity import ActivityLine
from sagex.widgets.message import ChatMessage
from sagex.widgets.run_graph import RunGraph
from sagex.widgets.prompt_input import PromptInput


class SagexApp(App):
    """The whole application. Everything hangs off this one class."""

    CSS_PATH = "app.tcss"                 # styling loaded from the file next to this one

    BINDINGS = [
        # Not a bare "q": you type straight into the transcript, so a letter key must
        # never quit (e.g. keystrokes while a command runs and the prompt is hidden).
        ("ctrl+q", "quit", "Quit"),
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
        self._selected_label: str | None = None                  # the highlighted resource, if any
        self._run_graph: RunGraph | None = None           # live graph of the running command
        self._load_gen = 0                                # bumps on every (re)load
        self._pending_loads: set = set()                  # branches still loading (this gen)

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
                yield ActivityLine(id="resources-activity")                # while branches load
                yield Static(id="resources-status")

            # Right: a terminal-style transcript. You type on the prompt line at its
            # end; new entries are inserted above it, so the prompt always stays last.
            with VerticalScroll(id="chat-log"):
                yield Static(self._welcome(), id="welcome")
                yield ActivityLine(id="command-activity")   # while a command runs
                with Horizontal(id="prompt-row"):
                    yield Static("❯ ", id="prompt-glyph")
                    yield PromptInput(
                        placeholder="Ask Autobot, or start with ! to run a shell command",
                        id="chat-input",
                        compact=True,
                    )
                yield Static(id="prompt-status")

        yield Footer()

    @staticmethod
    def _welcome() -> Text:
        text = Text()
        text.append("✻ ", style=ACCENT)
        text.append("Welcome to sagex", style="bold")
        text.append("  ·  Autosage in your terminal\n\n", style="bright_black")
        text.append("Type to ask Autobot · start with ", style="bright_black")
        text.append("!", style="bold")
        text.append(" to run a shell command, e.g. ", style="bright_black")
        text.append("!sagex list workflow\n", style="bold")
        text.append("Esc stops a run · ↑/↓ history · F2 shell · F3 server/local · F5 refresh · Ctrl+Q quit",
                    style="bright_black")
        return text

    def on_mount(self) -> None:
        """Runs once, right after the widgets exist. Good place for setup."""
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

        self._refresh_prompt()           # "shell · cwd" under the prompt line
        self._apply_env()                # show the right tree + status for the current env
        if self._env == "server":
            self._start_loaders()        # fetch server resources (concurrently, off-thread)
        self.query_one("#chat-input", PromptInput).focus()   # ready to type straight away

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        """Fires whenever the highlight lands on a tree node (arrows or mouse)."""
        node = event.node
        # A branch (category) CAN be expanded; a leaf (real item) CANNOT. A real item
        # becomes the "context" shown under the prompt.
        self._selected_label = None if node.allow_expand else str(node.label)
        self._refresh_prompt()

    def on_click(self, event: events.Click) -> None:
        """A click anywhere in the transcript puts the cursor back on the prompt."""
        log = self.query_one("#chat-log", VerticalScroll)
        if not self._busy and event.widget is not None and log in event.widget.ancestors_with_self:
            self.query_one("#chat-input", PromptInput).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Fires when the user presses Enter in an input box."""
        if event.input.id == "workspace-input":     # the local-env path box
            self._set_workspace(event.value)
            return

        text = event.value.strip()
        event.input.value = ""           # clear the line either way
        if not text:                     # ignore empty / whitespace-only lines
            return
        self.query_one("#chat-input", PromptInput).add_to_history(text)   # for ↑/↓ recall

        if text.startswith("!"):         # "!" prefix -> run as a shell command
            command = text[1:].strip()
            if not command:
                return
            if command.lower() in ("clear", "cls"):     # UI builtin: wipe the transcript
                log = self.query_one("#chat-log", VerticalScroll)
                for entry in [*log.query(ChatMessage), *log.query(RunGraph)]:
                    entry.remove()
                return
            self.add_message(command, "command")        # echo it, like a terminal does
            out = self._new_output_message()            # its output streams in below
            self._set_busy(True)
            self.execute_command(command, out)
        else:                            # otherwise -> agent prompt (placeholder for now)
            self.add_message(text, role="user")
            self.add_message(
                "(placeholder reply — I'm not connected to the server yet.)",
                role="autobot",
            )

    @work(thread=True)
    def execute_command(self, command: str, out: ChatMessage) -> None:
        """Run a shell command in a BACKGROUND THREAD, streaming its output into `out`.

        This runs off the main thread, so every UI touch goes through
        `call_from_thread`, which hands the work back to Textual's main loop.
        """
        def on_line(line: str) -> None:
            event = runview.parse_event_line(line)      # progress from `sagex run workflow`
            if event is not None:
                self.call_from_thread(self._on_run_event, out, event)
                return
            self.call_from_thread(self._append_output, out, line)

        try:
            code = self.session.run_streaming(command, on_line)
        except Exception as exc:         # e.g. the shell itself couldn't start
            self.call_from_thread(self._append_output, out, f"Failed to run command: {exc}")
            code = -1

        self.call_from_thread(self._finish_command, out, code)

    def _new_output_message(self) -> ChatMessage:
        """(main thread) Insert an empty cmd_output entry and return it."""
        widget = ChatMessage("", "cmd_output")
        self._mount_entry(widget)
        return widget

    def _mount_entry(self, widget) -> None:
        """(main thread) Insert a transcript entry above the prompt, then scroll to it."""
        log = self.query_one("#chat-log", VerticalScroll)
        log.mount(widget, before=self.query_one("#command-activity", ActivityLine))
        self.call_after_refresh(log.scroll_end, animate=False)

    def _set_busy(self, busy: bool) -> None:
        """While a command runs, the prompt line gives way to the activity line — as
        in a terminal — and comes back (with the cursor on it) when it finishes."""
        self._busy = busy
        activity = self.query_one("#command-activity", ActivityLine)
        self.query_one("#prompt-row").display = not busy
        self.query_one("#prompt-status").display = not busy
        log = self.query_one("#chat-log", VerticalScroll)
        if busy:
            activity.start("Running", "esc to stop")
            log.focus()                  # keys scroll the output instead of typing nowhere
        else:
            activity.stop()
            self.query_one("#chat-input", PromptInput).focus()
        self.call_after_refresh(log.scroll_end, animate=False)

    def _on_run_event(self, out: ChatMessage, event: dict) -> None:
        """(main thread) Create, then update, the live graph of a workflow run."""
        if event.get("t") == "start":
            view = runview.WorkflowRunView.from_snapshot(event.get("view") or {})
            self._run_graph = RunGraph(view)
            self.query_one("#chat-log", VerticalScroll).mount(self._run_graph, before=out)
        elif self._run_graph is not None:
            self._run_graph.apply(event)

    def _append_output(self, widget: ChatMessage, line: str) -> None:
        """(main thread) Append a line to an output entry and keep it in view."""
        widget.append_line(line)
        self.query_one("#chat-log", VerticalScroll).scroll_end(animate=False)

    def add_message(self, text: str, role: str) -> None:
        """Add an entry to the transcript (above the prompt) and scroll it into view."""
        self._mount_entry(ChatMessage(text, role))

    def action_stop_command(self) -> None:
        """Esc: stop the currently running command, if any."""
        if self._busy:
            self.query_one("#command-activity", ActivityLine).set("Stopping", "")
        self.session.cancel()            # kills the process; the worker then finishes

    def _finish_command(self, out: ChatMessage, code: int) -> None:
        """(main thread) A command finished: show how it ended and bring the prompt back."""
        out.set_exit_code(code)
        if out.is_empty:                 # a silent success leaves no empty block behind
            out.remove()
        if self._run_graph is not None:  # e.g. detached or crashed before a final event
            self._run_graph.freeze()
            self._run_graph = None
        self._set_busy(False)
        self._refresh_prompt()           # the working dir may have changed

    def action_cycle_shell(self) -> None:
        """F2: switch to the next installed shell (and remember the choice)."""
        self.session.cycle_shell()
        self.config["shell"] = self.session.shell        # persist across launches
        config.save(self.config)
        self._refresh_prompt()

    def _refresh_prompt(self) -> None:
        """The dim line under the prompt: 'shell · cwd', plus the highlighted resource."""
        status = Text(self.session.prompt, style="bright_black")
        if self._selected_label:
            status.append("  ·  context: ", style="bright_black")
            status.append(self._selected_label)
        self.query_one("#prompt-status", Static).update(status)

    def _start_loaders(self) -> None:
        """(Re)load every resource branch, resetting each to 'Loading…' first."""
        self._set_status("")             # clear any previous error
        self._load_gen += 1
        self._pending_loads = {id(node) for node, _ in self._loaders}
        self.query_one("#resources-activity", ActivityLine).start("Loading resources")
        for node, fetch in self._loaders:
            node.remove_children()
            node.add_leaf("Loading…")
            self._load_branch(node, fetch, self._load_gen)

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
            self._set_status("○ Not authenticated · sagex auth login")
        elif self._env == "local":
            self._set_status(f"local · {self._workspace}")
        else:
            self._set_status("")         # server ok; a load error will overwrite this

    def _set_status(self, text: str) -> None:
        """Set the one-line status bar under the resources tree."""
        self.query_one("#resources-status", Static).update(text)

    @work(thread=True)
    def _load_branch(self, node, fetch, gen: int) -> None:
        """Fetch display labels for a tree branch in a background thread.

        `fetch` takes an ApiClient and returns a list of labels (str or Text).
        """
        try:
            labels = fetch(build_client())
        except ApiError as exc:
            self.call_from_thread(self._fill_branch, node, None, exc.message, gen)
            return
        self.call_from_thread(self._fill_branch, node, labels, None, gen)

    def _fill_branch(self, node, labels, error, gen: int) -> None:
        """(main thread) Fill a branch with results; route any error to one status line."""
        if gen == self._load_gen:        # a stale load (before an F5) doesn't count
            self._pending_loads.discard(id(node))
            if not self._pending_loads:
                self.query_one("#resources-activity", ActivityLine).stop()
        node.remove_children()           # clear the "Loading…" placeholder
        if error:
            self._set_status(f"⚠ {error}  ·  F5 to retry")   # one shared message, not per-branch
            return
        if not labels:
            node.add_leaf("(none)")
        else:
            for label in labels:
                node.add_leaf(label)   # names already trimmed at construction (metadata kept)
