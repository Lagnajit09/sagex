"""Sage's tools, CLI side: the command index, what Sage may run, and running it.

Sage (the model) asks for sagex commands as word lists — ["show", "script",
"deploy.sh"]. This module is the only thing that decides what happens next:

- `check()` parses the words with sagex's real command parser (nothing runs)
  and classifies the command with the fixed table below. The model's choice of
  tool never makes a command read-only; a change asked through `sagex_read` is
  refused, and every change waits for the user's approval in the app (or an
  allowance they gave for this session — `Allowances`; deletes always ask).
- `CommandRun` runs an allowed command as `python -m sagex <words>`: an
  argument list, no shell, so nothing in the words is ever interpreted by a shell.
- `command_index()` is the compact command list sent to Sage once per
  connection. It's generated from the installed sagex, so it always matches.

Commands that need or print secrets are never available to Sage (see NEVER);
the user runs those with `!`.
"""

from __future__ import annotations

import functools
import os
import re
import shlex
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass

import typer

from sagex.runview import parse_event_line
from sagex.sage import scrub
from sagex.shell import clean_output

# Commands Sage can run with sagex_read: they only look things up.
READ_ONLY: frozenset[tuple[str, ...]] = frozenset({
    *(("list", kind) for kind in ("workflow", "script", "run", "trigger", "key", "server", "vault")),
    *(("show", kind) for kind in ("workflow", "script", "run", "trigger", "key", "server", "vault")),
    ("render", "workflow"),
    ("trigger", "invoke"),
    ("workspace", "show"),
    ("syntax",),
})

# Never available to Sage, by command prefix: they sign in, take secrets at a
# hidden prompt, or print a secret. They're left out of the index entirely.
NEVER: frozenset[tuple[str, ...]] = frozenset({
    ("auth",),
    ("create", "key"),
    ("update", "key"),
    ("copy", "key"),
    ("trigger", "create"),          # an HTTP trigger prints its secret once
    ("trigger", "regenerate"),      # prints the new secret
    ("trigger", "poll"),            # authenticates with the trigger's secret
})

# Options Sage may never pass (they reveal or read secrets), and options left
# out of the index because Sage doesn't need them (still accepted if used).
BLOCKED_OPTIONS = frozenset({"--reveal", "--secret-file"})
_UNLISTED_OPTIONS = frozenset({"--json", "--no-log", "--help"}) | BLOCKED_OPTIONS

READ_TIMEOUT = 120.0              # seconds; a read command that takes longer is stuck
CHANGE_TIMEOUT = 1800.0           # a change may follow a run live; the server waits 2700s
APPROVAL_TIMEOUT = 600.0          # unanswered approval cards decline themselves
RESULT_LINES = 150                # what Sage gets back: the end of the output
RESULT_CHARS = 6000


@dataclass(frozen=True)
class Check:
    """The outcome of checking a word list: a runnable command, or why not.

    `ref` is the command's first argument (the resource it acts on), if any.
    `always_ask`: deletes can't be allowed for the session. `takes_yes`: the
    command has its own confirmation prompt, which Sage's approval replaces.
    """

    path: tuple[str, ...] = ()
    read_only: bool = False
    error: str | None = None
    ref: str | None = None
    always_ask: bool = False
    takes_yes: bool = False


# --- the command tree --------------------------------------------------------

@functools.cache
def _root():
    # Imported lazily: sagex.__main__ imports the TUI, which imports this module.
    from sagex.__main__ import app

    return typer.main.get_command(app)


def _children(cmd) -> dict:
    return getattr(cmd, "commands", None) or {}


def _leaves(cmd=None, path: tuple[str, ...] = ()):
    cmd = cmd if cmd is not None else _root()
    subs = _children(cmd)
    if not subs:
        yield path, cmd
        return
    for name, sub in subs.items():
        yield from _leaves(sub, (*path, name))


def _is_never(path: tuple[str, ...]) -> bool:
    return any(path[:len(prefix)] == prefix for prefix in NEVER)


def _resolve(words: list[str]):
    """(path, command, remaining words), or an error message."""
    cmd, path = _root(), ()
    rest = list(words)
    while _children(cmd):
        if not rest or rest[0].startswith("-"):
            choices = "|".join(_children(cmd))
            where = f"`{' '.join(path)}` needs" if path else "Start with"
            return f"{where} one of: {choices}."
        name = rest.pop(0)
        sub = _children(cmd).get(name)
        if sub is None:
            where = f" under `{' '.join(path)}`" if path else ""
            return f"Unknown command {name!r}{where}. Choices: {'|'.join(_children(cmd))}."
        cmd, path = sub, (*path, name)
    return path, cmd, rest


def _option_name(word: str) -> str:
    return word.split("=", 1)[0]


def check(words: list[str]) -> Check:
    """Parse `words` with sagex's own parser and classify the command. Runs nothing."""
    if any(w in ("--help", "-h") for w in words):
        return Check(error="Use sagex_help for a command's help, not --help.")
    resolved = _resolve(words)
    if isinstance(resolved, str):
        return Check(error=resolved)
    path, cmd, rest = resolved
    shown = " ".join(path)
    if _is_never(path):
        return Check(path, error=f"`sagex {shown}` isn't available to Sage (it signs in, takes "
                                 f"or prints a secret). The user can run it with `!sagex {shown}`.")
    blocked = sorted({_option_name(w) for w in rest} & BLOCKED_OPTIONS)
    if blocked:
        return Check(path, error=f"{blocked[0]} isn't available to Sage.")
    try:
        ctx = cmd.make_context(f"sagex {shown}", list(rest))
    except typer.Exit:                       # an eager option (none left after the --help check)
        return Check(path, error="That option can't be used here.")
    except typer.TyperException as exc:      # usage errors: unknown option, missing argument…
        message = exc.format_message() if hasattr(exc, "format_message") else str(exc)
        return Check(path, error=f"`sagex {shown}`: {message}")
    arguments = [p for p in cmd.params if p.param_type_name == "argument"]
    ref = ctx.params.get(arguments[0].name) if arguments else None
    return Check(
        path, read_only=path in READ_ONLY,
        ref=str(ref) if ref not in (None, "", (), []) else None,
        always_ask=path[0] == "delete",
        takes_yes=any("--yes" in p.opts for p in cmd.params),
    )


# --- changes: describing them and what the user allowed ---------------------------

def describe(check: Check) -> str:
    """A plain one-line description of a change, from the command itself.

    Shown when Sage gave no summary; never depends on anything the model said.
    """
    path, ref = check.path, f" {check.ref}" if check.ref else ""
    if path == ("workspace", "set"):
        return f"Set the workspace folder to{ref}"
    if path[0] == "trigger" and len(path) > 1:
        return f"{path[1].capitalize()} the trigger for{ref}" if ref else f"{path[1].capitalize()} a trigger"
    if path[0] == "copy":
        return f"Copy {' '.join(path[1:])}{ref} into your workspace"
    if path[0] == "new":
        return f"Create a new local {' '.join(path[1:])} file{ref}"
    return f"{path[0].capitalize()} {' '.join(path[1:])}{ref}".rstrip()


def warning(check: Check) -> str | None:
    """What the approval card must say about a change, whatever Sage said."""
    if check.path == ("delete", "vault"):
        return "Deletes the vault and every server and credential in it. Can't be undone."
    if check.always_ask:
        return "Deletes it permanently. Can't be undone."
    return None


def approved_words(words: list[str], check: Check) -> list[str]:
    """The words to run once the user approved: the approval card was the
    confirmation, so the command's own prompt (which can't be answered from
    here) is skipped with --yes."""
    if check.takes_yes and not ({"--yes", "-y"} & set(words)):
        return [*words, "--yes"]
    return list(words)


class Allowances:
    """Commands the user let Sage run without asking, for this app session.

    Keyed by command and the resource it acts on — `run workflow deploy` — so
    allowing one workflow doesn't allow others; its options may vary. Deletes
    are never allowed this way. Held in memory only: a new session asks again.
    """

    def __init__(self) -> None:
        self._keys: list[tuple[str, ...]] = []

    @staticmethod
    def key(check: Check) -> tuple[str, ...]:
        return (*check.path, check.ref) if check.ref else check.path

    @staticmethod
    def label(key: tuple[str, ...]) -> str:
        return "sagex " + shlex.join(key)

    def can_allow(self, check: Check) -> bool:
        return not check.always_ask and not check.read_only and check.error is None

    def allows(self, check: Check) -> bool:
        return self.can_allow(check) and self.key(check) in self._keys

    def allow(self, check: Check) -> None:
        if self.can_allow(check) and self.key(check) not in self._keys:
            self._keys.append(self.key(check))

    def items(self) -> list[tuple[str, ...]]:
        return list(self._keys)

    def revoke(self, index: int) -> tuple[str, ...] | None:
        """Revoke the n-th allowance (1-based, as /permissions lists them)."""
        if 1 <= index <= len(self._keys):
            return self._keys.pop(index - 1)
        return None

    def clear(self) -> int:
        count, self._keys = len(self._keys), []
        return count

    def __len__(self) -> int:
        return len(self._keys)


# --- the index and help -------------------------------------------------------

def _first_sentence(text: str | None, limit: int = 90) -> str:
    line = (text or "").strip().split("\n", 1)[0]
    line = re.split(r"(?<=[.!?])\s", line, maxsplit=1)[0].rstrip(".")
    return line if len(line) <= limit else line[:limit - 1].rstrip() + "…"


def _placeholder(param) -> str:
    name = (param.metavar or param.name or "value").upper().strip("_")
    if param.multiple and name.endswith("S"):
        name = name[:-1]                     # --param PARAM…, not PARAMS…
    return name + ("…" if param.multiple else "")


def _signature(cmd, *, listed_only: bool = True) -> str:
    parts = []
    for p in cmd.params:
        if p.param_type_name == "argument":
            parts.append(f"<{p.name}>" if p.required else f"[{p.name}]")
            continue
        flag = max(p.opts, key=len)
        if flag in (_UNLISTED_OPTIONS if listed_only else BLOCKED_OPTIONS | {"--help"}):
            continue
        text = flag if p.is_flag else f"{flag} {_placeholder(p)}"
        parts.append(text if p.required else f"[{text}]")
    return " ".join(parts)


_RESOURCES = frozenset({"workflow", "script", "run", "trigger", "key", "server", "vault"})


def _index_lines(paths: list[tuple[tuple[str, ...], object]]) -> list[str]:
    """One line per command; sibling resource commands with the same signature
    share a line — `show workflow|trigger|server|vault <ref>` instead of four.
    (Only resources: `trigger enable|disable` would lose their own descriptions.)
    """
    groups: dict[tuple, list] = {}
    for path, cmd in paths:
        mergeable = len(path) > 1 and path[-1] in _RESOURCES
        key = (path[:-1], _signature(cmd)) if mergeable else (path, None)
        groups.setdefault(key, []).append((path, cmd))
    lines = []
    for members in groups.values():
        parent, signature = members[0][0][:-1], _signature(members[0][1])
        names = "|".join(p[-1] for p, _ in members)
        if len(members) > 1:
            group_cmd = _root()
            for word in parent:
                group_cmd = _children(group_cmd)[word]
            doc = _first_sentence(group_cmd.help)
        else:
            doc = _first_sentence(members[0][1].help)
        head = " ".join((*parent, names))
        lines.append(f"{head} {signature}".rstrip() + (f" — {doc}" if doc else ""))
    return lines


@functools.cache
def command_index() -> str:
    """The compact command list Sage gets once per connection (a few hundred tokens)."""
    usable = [(p, c) for p, c in _leaves() if not _is_never(p)]
    read = [(p, c) for p, c in usable if p in READ_ONLY]
    other = [(p, c) for p, c in usable if p not in READ_ONLY]
    # Headings say what the commands are, not which tool runs them: the server
    # decides that from the tools it accepted (an older server has no sagex_change).
    return "\n".join([
        "Read-only:",
        *_index_lines(read),
        "",
        "Changes (change things or run long):",
        *_index_lines(other),
        "",
        "<x> is required, [x] optional; X… may repeat. Full options: sagex_help.",
    ])


TOOLS = ("sagex_change", "sagex_help", "sagex_read")


def hello_frame() -> dict:
    """What this CLI offers Sage: its command index and the tools it can run."""
    return {"index": command_index(), "tools": list(TOOLS)}


def help_text(words: list[str]) -> tuple[str | None, str | None]:
    """(help, None) for one command, or (None, error)."""
    resolved = _resolve([w for w in words if not w.startswith("-")])
    if isinstance(resolved, str):
        return None, resolved
    path, cmd, _rest = resolved
    shown = " ".join(path)
    if _is_never(path):
        return None, (f"`sagex {shown}` isn't available to Sage. The user can run it with "
                      f"`!sagex {shown}`.")
    out = [f"sagex {shown} {_signature(cmd, listed_only=False)}".rstrip(), ""]
    out.append((cmd.help or "").strip())
    args = [p for p in cmd.params if p.param_type_name == "argument"]
    opts = [p for p in cmd.params if p.param_type_name != "argument"
            and max(p.opts, key=len) not in BLOCKED_OPTIONS | {"--help"}]
    if args:
        out += ["", "Arguments:"]
        out += [f"  <{p.name}>  {getattr(p, 'help', '') or ''}".rstrip() for p in args]
    if opts:
        out += ["", "Options:"]
        for p in opts:
            names = ", ".join(sorted(p.opts, key=len, reverse=True))
            if not p.is_flag:
                names += f" {_placeholder(p)}"
            extra = []
            if p.multiple:
                extra.append("repeatable")
            if p.required:
                extra.append("required")
            elif not p.is_flag and p.default not in (None, (), [], ""):
                extra.append(f"default: {p.default}")
            tail = f" [{', '.join(extra)}]" if extra else ""
            out.append(f"  {names}  {(p.help or '').strip()}{tail}".rstrip())
    out += ["", "Read-only." if path in READ_ONLY else
            "Changes things or runs long: the user approves it before it runs."]
    return "\n".join(out).strip(), None


# --- running -------------------------------------------------------------------

def result_tail(lines: list[str]) -> tuple[str, bool]:
    """The end of a command's output, as Sage receives it, and whether it was cut."""
    tail = lines[-RESULT_LINES:]
    text = "\n".join(tail)
    cut = len(tail) < len(lines)
    if len(text) > RESULT_CHARS:
        text, cut = text[-RESULT_CHARS:], True
    return scrub(text), cut


class CommandRun:
    """One sagex command run for Sage, cancellable from another thread."""

    def __init__(self, words: list[str], *, cwd: str | None = None, width: int = 100,
                 timeout: float = READ_TIMEOUT) -> None:
        self.words = list(words)
        self.cwd = cwd
        self.width = max(60, min(width, 200))
        self.timeout = timeout
        self.lines: list[str] = []
        self.timed_out = False
        # The installed sagex, run by the same Python as this app.
        self.program = [sys.executable, "-m", "sagex"]
        self._proc: subprocess.Popen | None = None
        self._cancelled = threading.Event()

    def run(self, on_line: Callable[[str], None],
            on_event: Callable[[dict], None] | None = None) -> int:
        """Blocking: run the command, calling on_line per output line; return the exit code.

        With `on_event`, a workflow run also reports its progress (the lines the
        TUI draws its live graph from); those go to on_event, not into the output.
        """
        if self._cancelled.is_set():
            return -1
        group = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt"
                 else {"start_new_session": True})
        env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "COLUMNS": str(self.width),
               "PYTHONIOENCODING": "utf-8"}
        if on_event is not None:
            env["SAGEX_EVENTS"] = "1"
        else:
            env.pop("SAGEX_EVENTS", None)        # plain output, no live-graph event lines
        self._proc = subprocess.Popen(
            [*self.program, *self.words],
            cwd=self.cwd, stdin=subprocess.DEVNULL,       # a prompt reads EOF instead of hanging
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=env, **group,
        )
        timer = threading.Timer(self.timeout, self._on_timeout)
        timer.daemon = True
        timer.start()
        try:
            for raw in self._proc.stdout:
                line = clean_output(raw.rstrip("\n"))
                if on_event is not None:
                    event = parse_event_line(line)
                    if event is not None:
                        on_event(event)
                        continue
                self.lines.append(line)
                on_line(line)
            self._proc.wait()
            return self._proc.returncode
        finally:
            timer.cancel()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def _on_timeout(self) -> None:
        self.timed_out = True
        self.cancel()

    def cancel(self) -> None:
        self._cancelled.set()
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            else:
                proc.kill()
        except Exception:
            pass


def result_frame(call_id: str, code: int, run: CommandRun, elapsed: float) -> dict:
    """The `tool_result` for a finished run."""
    if run.timed_out:
        return {"type": "tool_result", "id": call_id,
                "error": f"The command was stopped after {int(run.timeout)}s without finishing. "
                         "Anything it started on the server (e.g. a run) may still be going."}
    output, cut = result_tail(run.lines)
    return {"type": "tool_result", "id": call_id, "exit_code": code, "output": output,
            "truncated": cut, "seconds": round(elapsed, 1)}
