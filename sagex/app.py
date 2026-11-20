"""The sagex terminal application: layout and event wiring.

Appearance lives in app.tcss; status formatting in formatting.py. This file
focuses on WHAT widgets exist and HOW they react.
"""

import asyncio
import os
import platform
import shlex
import time

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Header, Footer, Tree, Input, Static, DirectoryTree

from sagex import config, local_tools, runview, sage_tools, shell
from sagex.api import ApiError, build_client, resources, store
from sagex.formatting import ACCENT, run_label, trigger_label
from sagex.sage import SageError, SageSession, command_context
from sagex.widgets.activity import ActivityLine
from sagex.widgets.message import ChatMessage
from sagex.widgets.run_graph import RunGraph
from sagex.widgets.prompt_input import PromptInput
from sagex.widgets.tool_card import ToolCard


def _one_line(value, limit: int = 200) -> str:
    """Sage's summary of a change, as one plain bounded line (it's model text)."""
    if not isinstance(value, str):
        return ""
    text = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


class SagexApp(App):
    """The whole application. Everything hangs off this one class."""

    CSS_PATH = "app.tcss"                 # styling loaded from the file next to this one
    TITLE = "sagex"                       # shown in the header (defaults to the class name)

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
        # Workspace = the one folder local resources live in (the "local"
        # environment). The terminal opens there, wherever sagex was started.
        ws = self.config.get("workspace")
        self._workspace = ws if ws and os.path.isdir(ws) else os.getcwd()
        self.session = shell.ShellSession(preferred=self.config.get("shell"), cwd=self._workspace)
        self._busy = False                                # is a command or Sage reply in progress?
        self._sage = SageSession(hello=sage_tools.hello_frame)   # connected on first use
        self._sage_turn = False                           # is the busy thing a Sage reply?
        self._sage_run: sage_tools.CommandRun | None = None   # a command Sage is running
        self._asking: ToolCard | None = None              # a change waiting for approval
        self._allowances = sage_tools.Allowances()        # changes allowed for this session
        self._last_command: dict | None = None            # context for the next Sage question
        self._selected_label: str | None = None                  # the highlighted resource, if any
        self._run_graph: RunGraph | None = None           # live graph of the running command
        self._load_gen = 0                                # bumps on every (re)load
        self._pending_loads: set = set()                  # branches still loading (this gen)

        # Start in the server environment if a key is stored, else local-only.
        self._env = "server" if store.get_key() else "local"

    def compose(self) -> ComposeResult:
        """Declare WHAT is on screen, top to bottom."""
        yield Header(show_clock=True)

        # Horizontal split: resources on the left, the terminal (commands + Sage) on the right.
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
                        placeholder="Ask Sage, or start with ! to run a command — e.g. !sagex list workflow",
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
        text.append("Start with ", style="bright_black")
        text.append("!", style="bold")
        text.append(" to run a command, e.g. ", style="bright_black")
        text.append("!sagex list workflow\n", style="bold")
        text.append("Anything else goes to ", style="bright_black")
        text.append("Sage", style=f"bold {ACCENT}")
        text.append(", your Autosage assistant — ask why your last command failed\n",
                    style="bright_black")
        text.append("Sage asks before it changes anything, opens a file or runs a shell command · ",
                    style="bright_black")
        text.append("/permissions", style="bold")
        text.append(" to review what you've allowed\n", style="bright_black")
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

        if text.startswith("/"):         # "/" prefix -> an app command (/permissions)
            self.add_message(text, "user")
            self._app_command(text[1:].split())
            return
        if text.startswith("!"):         # "!" prefix -> run as a shell command
            command = text[1:].strip()
            if not command:
                return
            if command.lower() in ("clear", "cls"):     # UI builtin: wipe the transcript
                log = self.query_one("#chat-log", VerticalScroll)
                for entry in [*log.query(ChatMessage), *log.query(RunGraph), *log.query(ToolCard)]:
                    entry.remove()
                return
            self.add_message(command, "command")        # echo it, like a terminal does
            out = self._new_output_message()            # its output streams in below
            self._set_busy(True)
            self.execute_command(command, out)
        else:                            # otherwise -> a question for Sage
            self.add_message(text, role="user")
            if not store.get_key():
                self.add_message("Sign in to talk to Sage: run !sagex auth login", role="notice")
                return
            context: dict = {"env": self._sage_env()}       # where Sage's tools act, this turn
            if self._last_command:
                context["last_command"] = self._last_command
            self._last_command = None    # shared once; Sage remembers it from here
            self._sage_turn = True
            self._set_busy(True, "Thinking")
            self.ask_sage(text, context)

    def _app_command(self, words: list[str]) -> None:
        """`/permissions` — see and revoke the changes Sage may make without asking."""
        name, args = (words[0].lower(), words[1:]) if words else ("", [])
        if name != "permissions":
            self.add_message(f"Unknown command /{name}. Available: /permissions", "notice")
            return
        allowances = self._allowances
        usage = "Usage: /permissions · /permissions revoke <number> · /permissions revoke all"
        if not args:
            if not len(allowances):
                self.add_message("Sage asks before every change, file access and shell command. "
                                 "Press a on an approval card to let it do that again without "
                                 "asking, this session.", "cmd_output")
                return
            lines = ["Sage may do these without asking, until you quit sagex:"]
            for i, label in enumerate(allowances.items(), 1):
                tail = "  (any options)" if label.startswith("`sagex ") else ""
                lines.append(f"  {i}. {label.replace('`', '')}{tail}")
            lines += ["Deletes and risky shell commands always ask.",
                      "Revoke: /permissions revoke <number> · /permissions revoke all"]
            self.add_message("\n".join(lines), "cmd_output")
        elif len(args) == 2 and args[0].lower() == "revoke":
            if args[1].lower() == "all":
                count = allowances.clear()
                self.add_message(f"Revoked {count} — Sage asks before everything again.", "notice")
            else:
                label = allowances.revoke(int(args[1])) if args[1].isdigit() else None
                if label is None:
                    self.add_message(f"No allowance {args[1]!r}. See /permissions.", "notice")
                else:
                    self.add_message(f"Revoked: {label.replace('`', '')} — Sage asks before "
                                     "doing it again.", "notice")
        else:
            self.add_message(usage, "notice")
        self._refresh_prompt()

    @work(group="sage")
    async def ask_sage(self, text: str, context: dict | None) -> None:
        """Stream Sage's reply into the transcript (on the app's own event loop)."""
        activity = self.query_one("#command-activity", ActivityLine)
        reply: ChatMessage | None = None
        try:
            async for frame in self._sage.turn(text, context):
                kind = frame.get("type")
                if kind == "token":
                    if reply is None:
                        reply = ChatMessage("", "sage")
                        self._mount_entry(reply)
                        activity.set("Responding")
                    reply.append_text(frame.get("text", ""))
                    self.query_one("#chat-log", VerticalScroll).scroll_end(animate=False)
                elif kind == "tool_call":
                    reply = None             # text after the tool goes in a new entry, below it
                    result = await self._run_sage_tool(frame)
                    await self._sage.send_tool_result(result)
                    activity.set("Thinking")
                elif kind == "reconnected":
                    self.add_message("Started a new conversation — Sage doesn't remember the "
                                     "earlier one yet.", role="notice")
                elif kind == "notice":
                    self.add_message(frame.get("message") or "", role="notice")
                elif kind == "done" and frame.get("cancelled"):
                    self.add_message("Stopped.", role="notice")
                elif kind == "done" and frame.get("limit_reached"):
                    self.add_message("Sage reached its step limit for one request. Say "
                                     "\"continue\" to let it carry on from here.", role="notice")
                elif kind == "error":
                    self.add_message(frame.get("message") or "Sage couldn't answer.", role="notice")
        except SageError as exc:
            self.add_message(exc.message, role="notice")
        finally:
            self._sage_turn = False
            self._set_busy(False)

    async def _run_sage_tool(self, frame: dict) -> dict:
        """Show one Sage tool call as a card, run it if allowed, return its tool_result.

        Whether a command may run is decided here (sage_tools.check), never by
        which tool Sage picked.
        """
        call_id = str(frame.get("id") or "")
        name = str(frame.get("name") or "")
        if name in ("read_file", "write_file", "edit_file", "delete_file"):
            return await self._run_file_tool(call_id, name, frame)
        if name == "run_shell":
            return await self._run_shell_tool(call_id, frame)
        words = [str(w) for w in frame.get("args") or []]

        def refused(card: ToolCard, reason: str) -> dict:
            card.refuse(reason)
            return {"type": "tool_result", "id": call_id, "error": reason}

        if name == "sagex_help":
            card = ToolCard(name, "sagex help " + shlex.join(words), folded_lines=0)
            self._mount_entry(card)
            text, error = sage_tools.help_text(words)
            if error:
                return refused(card, error)
            card.set_lines(text.splitlines())
            card.finish(0, None, sage_saw="Sage read it")
            return {"type": "tool_result", "id": call_id, "exit_code": 0, "output": text}

        if name not in ("sagex_read", "sagex_change"):
            card = ToolCard(name, "sagex " + shlex.join(words))
            self._mount_entry(card)
            return refused(card, f"{name} isn't available in this version of sagex.")

        check = sage_tools.check(words)
        change = check.error is None and not check.read_only
        summary = warning = None
        if change and name == "sagex_change":
            words = sage_tools.approved_words(words, check)   # the card shows exactly what runs
            summary = _one_line(frame.get("summary")) or sage_tools.describe(check)
            warning = sage_tools.warning(check)
        card = ToolCard(name, "sagex " + shlex.join(words), summary=summary, warning=warning)
        self._mount_entry(card)
        if check.error:
            return refused(card, check.error)
        if change and name == "sagex_read":
            return refused(card, f"`sagex {' '.join(check.path)}` changes things or runs long, so "
                                 "sagex_read can't run it. Use sagex_change.")
        if change:
            grant = sage_tools.grant_for(check)
            declined = await self._approve(card, grant, "run it" if grant.allowable else "delete it")
            if declined:
                return {"type": "tool_result", "id": call_id, "error": declined}

        run = sage_tools.CommandRun(
            words, cwd=self._sage_workspace(), width=self._card_width(),
            timeout=sage_tools.CHANGE_TIMEOUT if change else sage_tools.READ_TIMEOUT)
        return await self._stream_run(call_id, card, run, f"Running sagex {' '.join(check.path)}",
                                      live_graph=change)

    async def _stream_run(self, call_id: str, card: ToolCard, run: sage_tools.CommandRun,
                          activity: str, *, live_graph: bool = False) -> dict:
        """Run a command for Sage, streaming its output into its card; its tool_result."""
        self._sage_run = run
        self.query_one("#command-activity", ActivityLine).set(activity)
        started = time.monotonic()
        try:
            code = await asyncio.to_thread(
                run.run,
                lambda line: self.call_from_thread(self._append_card_line, card, line),
                (lambda event: self.call_from_thread(card.run_event, event)) if live_graph else None)
        except OSError as exc:
            reason = f"Couldn't start it: {exc}"
            card.refuse(reason)
            return {"type": "tool_result", "id": call_id, "error": reason}
        finally:
            self._sage_run = None
        elapsed = time.monotonic() - started

        result = sage_tools.result_frame(call_id, code, run, elapsed)
        if run.timed_out:
            card.stop(f"stopped after {int(run.timeout)}s")
        elif run.cancelled:
            card.stop("stopped")
        else:
            saw = (f"Sage saw the last {sage_tools.RESULT_LINES} lines" if result.get("truncated")
                   else "Sage saw all of it")
            card.finish(code, elapsed, sage_saw=saw)
        return result

    # --- Sage's local tools: files and the shell ---------------------------------------

    def _sage_workspace(self) -> str:
        """Where everything Sage does locally happens: sagex commands, files, shell.

        Read fresh, so `!sagex workspace set` mid-session counts; never the
        terminal's current folder (it's where you `cd`, not where resources live).
        """
        ws = config.load().get("workspace")
        return ws if ws and os.path.isdir(ws) else self._workspace

    def _sage_env(self) -> dict:
        """Where Sage's tools act this turn — sent with every question."""
        return {"os": f"{platform.system()} {platform.release()}".strip(),
                "shell": self.session.shell, "workspace": self._sage_workspace()}

    def _card_width(self) -> int:
        return self.query_one("#chat-log", VerticalScroll).size.width - 6

    async def _run_file_tool(self, call_id: str, name: str, frame: dict) -> dict:
        """read_file / write_file / edit_file / delete_file: checked here, approved on the card.

        Writes and edits of one file share an allowance ("writing scripts/a.sh")."""
        verb = name.split("_")[0]                       # read | write | edit | delete
        raw = str(frame.get("path") or "")
        summary = _one_line(frame.get("summary")) or None

        def result(output: str) -> dict:
            return {"type": "tool_result", "id": call_id, "exit_code": 0, "output": output}

        def refused(card: ToolCard, reason: str) -> dict:
            card.refuse(reason)
            return {"type": "tool_result", "id": call_id, "error": reason}

        try:
            target = local_tools.resolve(raw, workspace=self._sage_workspace(),
                                         extra_protected=(str(config.CONFIG_DIR),))
        except local_tools.LocalToolError as exc:
            card = ToolCard(name, f"{verb} {raw}", glyph="▸ ")
            self._mount_entry(card)
            return refused(card, str(exc))

        if name == "read_file":
            offset = frame.get("offset")
            card = ToolCard(name, f"read {target.shown}", glyph="▸ ")
            self._mount_entry(card)
            try:                                        # local only: nothing reaches Sage before approval
                read = local_tools.read(target, offset if isinstance(offset, int) else 1)
            except (local_tools.LocalToolError, OSError) as exc:
                return refused(card, str(exc))
            card.set_lines(read.lines)                  # what Sage would see
            folder = target.path if os.path.isdir(target.path) else os.path.dirname(target.path)
            grant = sage_tools.Grant(("read", os.path.normcase(folder)), f"reading files in {folder}")
            declined = await self._approve(card, grant, "let Sage read it")
            if declined:
                return {"type": "tool_result", "id": call_id, "error": declined}
            card.finish(0, None, sage_saw=f"Sage saw {read.seen}")
            return result(read.output)

        if name in ("write_file", "edit_file"):
            editing = name == "edit_file"
            try:
                plan = (local_tools.plan_edit(target, frame.get("edits")) if editing
                        else local_tools.plan_write(target, frame.get("content")))
            except (local_tools.LocalToolError, OSError) as exc:
                card = ToolCard(name, f"{verb} {target.shown}", summary=summary, glyph="▸ ")
                self._mount_entry(card)
                return refused(card, str(exc))
            overwrite = plan.exists and not plan.unchanged and not editing
            card = ToolCard(name, plan.headline,
                            summary=summary or f"{verb.capitalize()} {target.shown}",
                            warning="Replaces the current file — the changes are below."
                            if overwrite else None, glyph="▸ ")
            self._mount_entry(card)
            if plan.unchanged:
                card.finish(0, None, sage_saw="already up to date")
                return result(f"No change: {target.shown} already has exactly this content.")
            card.diff = plan.exists
            card.set_lines(plan.preview)
            grant = sage_tools.Grant(("write", os.path.normcase(target.path)), f"writing {target.shown}")
            declined = await self._approve(card, grant, "make the change" if editing else "write it")
            if declined:
                return {"type": "tool_result", "id": call_id, "error": declined}
            try:
                message = local_tools.write(plan)
            except OSError as exc:
                return refused(card, f"Couldn't write {target.shown}: {exc.strerror or exc}")
            card.finish(0, None, sage_saw="written")
            self._reload_workspace_tree()
            return result(message)

        # delete_file
        card = ToolCard(name, f"delete {target.shown}", glyph="▸ ",
                        summary=summary or f"Delete {target.shown}",
                        warning="Deletes the file from your disk (not to the recycle bin). "
                                "Can't be undone.")
        self._mount_entry(card)
        try:
            local_tools.check_delete(target)
        except local_tools.LocalToolError as exc:
            return refused(card, str(exc))
        grant = sage_tools.Grant(("delete", os.path.normcase(target.path)), target.shown, allowable=False)
        declined = await self._approve(card, grant, "delete it")
        if declined:
            return {"type": "tool_result", "id": call_id, "error": declined}
        try:
            message = local_tools.delete(target)
        except OSError as exc:
            return refused(card, f"Couldn't delete {target.shown}: {exc.strerror or exc}")
        card.finish(0, None, sage_saw="deleted")
        self._reload_workspace_tree()
        return result(message)

    async def _run_shell_tool(self, call_id: str, frame: dict) -> dict:
        """run_shell: one non-interactive command in the user's shell, after approval."""
        command = str(frame.get("command") or "").strip()
        summary = _one_line(frame.get("summary")) or None
        shell_name = self.session.shell
        risk = local_tools.shell_risk(command)
        card = ToolCard("run_shell", command, summary=summary, warning=risk.warning,
                        glyph=f"{shell_name} ❯ ")
        self._mount_entry(card)
        try:
            local_tools.check_shell(command)
        except local_tools.LocalToolError as exc:
            card.refuse(str(exc))
            return {"type": "tool_result", "id": call_id, "error": str(exc)}
        grant = sage_tools.Grant(("shell", shell_name, command), f"`{command}` in {shell_name}",
                                 allowable=not risk.always_ask)
        declined = await self._approve(card, grant, "run it")
        if declined:
            return {"type": "tool_result", "id": call_id, "error": declined}
        run = sage_tools.CommandRun(
            [], cwd=self._sage_workspace(), width=self._card_width(), timeout=local_tools.SHELL_TIMEOUT,
            argv=self.session.invocation(command), env=local_tools.shell_env(dict(os.environ)))
        return await self._stream_run(call_id, card, run, f"Running {command[:60]}")

    def _reload_workspace_tree(self) -> None:
        try:
            self.query_one("#workspace-tree", DirectoryTree).reload()
        except Exception:
            pass                                        # cosmetic only

    async def _approve(self, card: ToolCard, grant: sage_tools.Grant, yes_label: str) -> str | None:
        """Ask the user to approve a tool call on its card. None when approved,
        else the reason it didn't happen (sent back to Sage)."""
        allowed_note = "allowed for this session · /permissions to change"
        if self._allowances.allows(grant):
            card.approved(allowed_note)
            return None
        choices = [("y", yes_label)]
        if grant.allowable:
            choices.append(("a", f"allow {grant.label} this session"))
        choices.append(("n", "no"))

        activity = self.query_one("#command-activity", ActivityLine)
        activity.set("Waiting for your approval", " · ".join(k for k, _ in choices) + " · esc to stop")
        self._asking = card
        try:
            answer = await asyncio.wait_for(card.ask(choices), sage_tools.APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            answer = "timeout"
        finally:
            self._asking = None
            card.withdraw()                  # no-op once answered
            activity.set("Thinking", "esc to stop")
            self.query_one("#chat-log", VerticalScroll).focus()

        if answer == "y":
            card.approved("approved once")
            return None
        if answer == "a":
            self._allowances.allow(grant)
            card.approved(allowed_note)
            self._refresh_prompt()
            return None
        if answer == "timeout":
            card.decline(f"no answer in {int(sage_tools.APPROVAL_TIMEOUT // 60)} minutes")
            return "The user didn't answer the approval in time, so it didn't run."
        if answer is None:
            card.decline("stopped")
            return "The user stopped this before it ran."
        card.decline("you said no")
        return ("The user chose not to run this. Don't retry it or do the same thing another "
                "way; ask what they'd like instead.")

    def _append_card_line(self, card: ToolCard, line: str) -> None:
        card.append_line(line)
        self.query_one("#chat-log", VerticalScroll).scroll_end(animate=False)

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

        self.call_from_thread(self._finish_command, out, code, command)

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

    def _set_busy(self, busy: bool, verb: str = "Running") -> None:
        """While a command runs (or Sage answers), the prompt line gives way to the
        activity line — as in a terminal — and comes back (with the cursor on it)
        when it finishes."""
        self._busy = busy
        activity = self.query_one("#command-activity", ActivityLine)
        self.query_one("#prompt-row").display = not busy
        self.query_one("#prompt-status").display = not busy
        log = self.query_one("#chat-log", VerticalScroll)
        if busy:
            activity.start(verb, "esc to stop")
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
        """Esc: stop the running command, or Sage's reply, if any."""
        if self._busy:
            self.query_one("#command-activity", ActivityLine).set("Stopping", "")
        if self._sage_turn:
            if self._asking is not None:
                self._asking.withdraw()      # a pending approval ends as "not run"
            if self._sage_run is not None:
                self._sage_run.cancel()      # a command Sage is running stops too
            self.run_worker(self._sage.cancel(), group="sage-cancel")   # Sage replies done{cancelled}
            return
        self.session.cancel()            # kills the process; the worker then finishes

    async def on_unmount(self) -> None:
        await self._sage.close()

    def _finish_command(self, out: ChatMessage, code: int, command: str = "") -> None:
        """(main thread) A command finished: show how it ended and bring the prompt back."""
        # Remember it so a follow-up question to Sage ("why did that fail?") has context.
        self._last_command = command_context(command, code, out.body)
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
        if len(self._allowances):
            status.append(f"  ·  Sage may run {len(self._allowances)} without asking (/permissions)",
                          style="yellow")
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
        self.session.cwd = resolved          # the terminal follows: one place for local work
        self._refresh_prompt()
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
