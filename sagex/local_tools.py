"""Sage's local tools, CLI side: files in the workspace and shell commands.

Like sagex commands (sage_tools), these only ever run on the user's machine,
and only after the user approves the call on its card (or allowed it for the
session). This module decides what may be asked at all; the app asks.

Files
- Only inside the workspace folder or the folder the terminal is in (after
  resolving symlinks, so a link can't point Sage outside them).
- Never files that typically hold secrets (.env, private keys, certificates,
  credential stores) or folders like .git and .ssh — a secret read by Sage
  would be sent to the model.
- `read_file` returns numbered lines a page at a time (or a folder's entries);
  obvious secret values are masked, and the model is told so, and a write
  that would put those masks back over the real values is refused.
- `write_file` replaces the whole file; the card previews the new file, or a
  diff when it overwrites one. `edit_file` replaces exact text that must occur
  once (all edits or none), previewed as a diff. `delete_file` deletes single
  files, always asking.

Shell
- One non-interactive command in the user's chosen shell, in their current
  folder, stdin closed, bounded in time. Commands that print environment
  variables or secret files, or that call sagex (which has its own checked
  tools), are refused before anyone is asked; destructive ones always ask and
  carry a warning that comes from here, not from the model.
"""

from __future__ import annotations

import difflib
import os
import re
import tempfile
from dataclasses import dataclass, field

from sagex.sage import scrub
from sagex.shell import is_interactive

MAX_READ_BYTES = 2_000_000        # larger files aren't read (logs, dumps…)
READ_PAGE_CHARS = 6000            # one read_file result (the server caps at 8000)
MAX_LIST_ENTRIES = 200
MAX_WRITE_CHARS = 200_000
SHELL_TIMEOUT = 600.0             # an install can be slow; a stuck command shouldn't hang the turn
MASK = "*****"

# Folders whose contents Sage never touches (anywhere in the path).
_PROTECTED_DIRS = frozenset({".git", ".ssh", ".aws", ".azure", ".gnupg", ".kube", ".docker",
                             ".sagex", ".config/gcloud"})
# File names that typically hold secrets.
_SECRET_FILE = re.compile(
    r"""(?ix)^(
        \.env(\.[\w.-]+)?                                   # .env, .env.production
      | .*\.(pem|key|p12|pfx|jks|keystore|kdbx|ppk|gpg|asc|ovpn|tfstate|tfvars)
      | id_(rsa|dsa|ecdsa|ed25519)(\.pub)?
      | \.netrc | \.pgpass | \.npmrc | \.pypirc | \.git-credentials | \.htpasswd
      | credentials(\.[\w-]+)? | service[-_]account.*\.json | gcs_key\.json
      | .*secrets?(\.[\w-]+)?\.(json|ya?ml|toml|ini|txt|env|cfg)
    )$""")
# …except the templates people commit on purpose.
_SAFE_TEMPLATE = re.compile(r"(?i)^\.env\.(example|sample|template|dist)$")
# Scripts that run on Linux must keep LF line endings.
_LF_ONLY = frozenset({".sh", ".bash", ".py"})


class LocalToolError(Exception):
    """Why a local tool call can't run; the message goes back to Sage."""


# --- paths ---------------------------------------------------------------------

def _norm(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:                        # different drives on Windows
        return False


def is_secret_name(name: str) -> bool:
    return bool(_SECRET_FILE.match(name)) and not _SAFE_TEMPLATE.match(name)


@dataclass(frozen=True)
class LocalPath:
    """A path Sage may use: absolute, and how to show it (relative to its root)."""

    path: str
    shown: str
    folder: str                               # shown form of the containing folder ("." for the root)


def resolve(raw: str, *, workspace: str, extra_protected: tuple[str, ...] = ()) -> LocalPath:
    """Resolve a path Sage gave (relative paths are under the workspace), or
    LocalToolError if it's outside the workspace or protected. The workspace is
    the only place local resources live; the terminal's current folder never counts."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text or "\x00" in text:
        raise LocalToolError("Give a file path, relative to the workspace.")
    if text.startswith("~") or "$" in text or "%" in text:
        raise LocalToolError("Use a plain path relative to the workspace (no ~ or variables).")
    candidate = text if os.path.isabs(text) else os.path.join(workspace, text)
    real = os.path.realpath(candidate)
    # Containment is compared case-folded (Windows); paths are shown as they are.
    root = os.path.realpath(workspace)
    if not _inside(os.path.normcase(real), os.path.normcase(root)):
        raise LocalToolError(
            f"{text} is outside the workspace ({workspace}). Sage can only use files in the "
            "workspace; the user can change it with F3 or `sagex workspace set`.")
    rel = os.path.relpath(real, root)
    parts = [p.lower() for p in rel.replace("\\", "/").split("/")]
    joined = "/".join(parts)
    if any(p in _PROTECTED_DIRS for p in parts) or any(
            f"/{d}/" in f"/{joined}/" for d in _PROTECTED_DIRS if "/" in d):
        raise LocalToolError(f"{text} is in a folder Sage never touches (it may hold secrets or "
                             "repository internals). The user can handle it directly.")
    if any(_inside(_norm(real), _norm(p)) for p in extra_protected):
        raise LocalToolError(f"{text} is sagex's own settings; Sage never touches them.")
    name = os.path.basename(real)
    if name and is_secret_name(name):
        raise LocalToolError(f"{name} usually holds secrets, so Sage never reads or changes it. "
                             "The user can edit it directly.")
    shown = "." if rel == "." else rel.replace("\\", "/")
    folder = os.path.dirname(shown) or "."
    return LocalPath(path=real, shown=shown, folder=folder)


# --- reading ---------------------------------------------------------------------

def _is_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _list_folder(target: LocalPath) -> str:
    try:
        entries = sorted(os.scandir(target.path), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as exc:
        raise LocalToolError(f"Couldn't list {target.shown}: {exc.strerror or exc}") from exc
    lines = []
    for entry in entries[:MAX_LIST_ENTRIES]:
        if entry.is_dir():
            lines.append(f"{entry.name}/")
        else:
            try:
                size = entry.stat().st_size
            except OSError:
                size = 0
            lines.append(f"{entry.name}  ({size} bytes)")
    more = len(entries) - len(lines)
    head = f"Folder {target.shown}/ — {len(entries)} entries" + (f", first {len(lines)} shown" if more else "")
    return head + ":\n" + ("\n".join(lines) if lines else "(empty)")


@dataclass
class ReadResult:
    output: str                               # what Sage receives
    lines: list[str] = field(default_factory=list)    # what the card shows
    seen: str = ""                            # "lines 1–120 of 120", for the card's footer


def read(target: LocalPath, offset: int = 1) -> ReadResult:
    """A page of a text file as numbered lines, or a folder's entries."""
    if os.path.isdir(target.path):
        text = _list_folder(target)
        return ReadResult(text, text.splitlines(), "the folder listing")
    if not os.path.isfile(target.path):
        raise LocalToolError(f"There's no file at {target.shown}.")
    size = os.path.getsize(target.path)
    if size > MAX_READ_BYTES:
        raise LocalToolError(f"{target.shown} is {size // 1_000_000} MB — too large to read.")
    with open(target.path, "rb") as fh:
        data = fh.read()
    if _is_binary(data):
        raise LocalToolError(f"{target.shown} is a binary file; Sage reads text files only.")
    all_lines = data.decode("utf-8-sig", errors="replace").splitlines()
    total = len(all_lines)
    start = max(1, offset)
    if total and start > total:
        raise LocalToolError(f"{target.shown} has only {total} lines.")
    out, used, end = [], 0, start - 1
    for number in range(start, total + 1):
        line = f"{number:>5}  {all_lines[number - 1]}"
        if out and used + len(line) + 1 > READ_PAGE_CHARS:
            break
        out.append(line[:READ_PAGE_CHARS])
        used += len(line) + 1
        end = number
    body = scrub("\n".join(out))
    masked = body.count(MASK) - "\n".join(out).count(MASK)
    seen = f"lines {start}–{end} of {total}" if total else "an empty file"
    head = f"{target.shown} — {seen}"
    notes = []
    if end < total:
        notes.append(f"More: read_file with offset={end + 1}.")
    if masked > 0:
        notes.append(f"{masked} value(s) that look like secrets are shown as {MASK}. Never write "
                     "the file back with those masks — leave those lines to the user.")
    text = head + ("\n" + " ".join(notes) if notes else "") + "\n" + (body or "(empty file)")
    return ReadResult(text, all_lines[start - 1:end], seen)


# --- writing ---------------------------------------------------------------------

@dataclass
class WritePlan:
    target: LocalPath
    content: str
    exists: bool
    preview: list[str]                        # the new file, or a diff for an overwrite
    unchanged: bool = False
    edits: int = 0                            # an edit_file plan: how many replacements

    @property
    def line_count(self) -> int:
        return len(self.content.splitlines())

    @property
    def headline(self) -> str:
        if self.edits:
            return f"edit {self.target.shown}  ({self.edits} change{'s' if self.edits > 1 else ''})"
        if self.exists:
            return f"write {self.target.shown}  (overwrite, {self.line_count} lines)"
        return f"write {self.target.shown}  (new file, {self.line_count} lines)"


def plan_write(target: LocalPath, content: str) -> WritePlan:
    """Check a write and build its preview. Nothing is written."""
    if not isinstance(content, str):
        raise LocalToolError("`content` must be the file's full text.")
    if len(content) > MAX_WRITE_CHARS:
        raise LocalToolError(f"That's more than {MAX_WRITE_CHARS} characters — too large to write.")
    if os.path.isdir(target.path):
        raise LocalToolError(f"{target.shown} is a folder.")
    lf_only = os.path.splitext(target.path)[1].lower() in _LF_ONLY
    content = content.replace("\r\n", "\n")
    if content and not content.endswith("\n"):
        content += "\n"
    exists = os.path.isfile(target.path)
    old = ""
    if exists:
        old = _existing_text(target)
        if MASK in content and MASK not in old:
            raise LocalToolError(
                f"The new content contains {MASK} — masked secret values from reading the file. "
                "Writing it would replace the real values. Leave those lines unchanged by asking "
                "the user to make this edit, or write the file without them.")
        # Keep a Windows file's CRLF endings (models write \n); scripts that run
        # on Linux always get LF, which fixes a CRLF one.
        if not lf_only and "\r\n" in old:
            content = content.replace("\n", "\r\n")
        if old == content:
            return WritePlan(target, content, True, [], unchanged=True)
        preview = _diff(old, content)
    else:
        preview = content.splitlines()
    return WritePlan(target, content, exists, preview)


def _existing_text(target: LocalPath) -> str:
    with open(target.path, "rb") as fh:
        data = fh.read(MAX_READ_BYTES + 1)
    if _is_binary(data) or len(data) > MAX_READ_BYTES:
        raise LocalToolError(f"{target.shown} is binary or very large; Sage won't change it.")
    return data.decode("utf-8-sig", errors="replace")


def _diff(old: str, new: str) -> list[str]:
    preview = [line.rstrip("\n") for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), "current", "new", n=2, lineterm="")][2:]
    return preview or ["(only the line endings change, to LF)"]


_LINE_NUMBER = re.compile(r"^\s*\d+  ")      # read_file's "   12  " prefix


def plan_edit(target: LocalPath, edits: list) -> WritePlan:
    """Apply exact-text replacements to an existing file, in order, and build
    the diff preview. Each `old_text` must occur exactly once. Nothing is written."""
    if os.path.isdir(target.path):
        raise LocalToolError(f"{target.shown} is a folder.")
    if not os.path.isfile(target.path):
        raise LocalToolError(f"There's no file at {target.shown}; use write_file to create it.")
    if not isinstance(edits, list) or not edits:
        raise LocalToolError("Give at least one edit {old_text, new_text}.")
    old = _existing_text(target)
    lf_only = os.path.splitext(target.path)[1].lower() in _LF_ONLY
    text = old.replace("\r\n", "\n")
    for n, edit in enumerate(edits, 1):
        before = str((edit or {}).get("old_text") or "").replace("\r\n", "\n")
        after = str((edit or {}).get("new_text") or "").replace("\r\n", "\n")
        label = f"Edit {n}" if len(edits) > 1 else "The edit"
        if not before:
            raise LocalToolError(f"{label} has an empty old_text.")
        if MASK in before + after and MASK not in old:
            raise LocalToolError(
                f"{label} contains {MASK} — a masked secret value from reading the file. Sage "
                "can't change those lines; leave them to the user.")
        count = text.count(before)
        if count == 0:
            hint = (" It looks like it includes read_file's line numbers; leave those out."
                    if _LINE_NUMBER.match(before) else "")
            raise LocalToolError(
                f"{label}: old_text wasn't found in {target.shown}.{hint} Copy it exactly from a "
                "fresh read_file — whitespace and indentation matter. Nothing was changed.")
        if count > 1:
            raise LocalToolError(
                f"{label}: old_text appears {count} times in {target.shown}. Include more "
                "surrounding lines so it matches once. Nothing was changed.")
        text = text.replace(before, after, 1)
    if "\r\n" in old and not lf_only:
        text = text.replace("\n", "\r\n")
    if text == old:
        return WritePlan(target, text, True, [], unchanged=True, edits=len(edits))
    return WritePlan(target, text, True, _diff(old, text), edits=len(edits))


def write(plan: WritePlan) -> str:
    """Write the file (atomically: a temp file in the same folder, then a rename)."""
    folder = os.path.dirname(plan.target.path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".sagex-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(plan.content)
        os.replace(tmp, plan.target.path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    if plan.edits:
        changes = f"{plan.edits} change{'s' if plan.edits > 1 else ''}"
        return f"Edited {plan.target.shown} ({changes}; now {plan.line_count} lines)."
    verb = "Overwrote" if plan.exists else "Created"
    return f"{verb} {plan.target.shown} ({plan.line_count} lines, {len(plan.content.encode())} bytes)."


def check_delete(target: LocalPath) -> None:
    if os.path.isdir(target.path):
        raise LocalToolError(f"{target.shown} is a folder; Sage deletes single files only.")
    if not os.path.isfile(target.path):
        raise LocalToolError(f"There's no file at {target.shown}.")


def delete(target: LocalPath) -> str:
    os.remove(target.path)
    return f"Deleted {target.shown}."


# --- shell -----------------------------------------------------------------------

# sagex in command position (not e.g. `pip install --upgrade sagex-cli`).
_SAGEX_IN_SHELL = re.compile(r"(?i)((^|[;&|(`]|&&|\|\|)\s*(\S*[\\/])?sagex(\.exe)?|-m\s+sagex)(?=$|[\s;&|)])")
_ENV_DUMP = re.compile(
    r"""(?ix)
      (^|[;&|]\s*)(env|printenv|set|export\s+-p|declare\s+-x|compgen\s+-v)\s*($|[;&|>])
    | \b(get-childitem|gci|dir|ls|get-item)\s+env:
    | \[(system\.)?environment\]::getenvironmentvariables
    | \$env:\w*(key|token|secret|pass|pwd|credential)
    | \b(cat|type|get-content|gc|more|head|tail|less)\b[^;&|]*\bSAGEX_
    """)
_DESTRUCTIVE = [
    (re.compile(r"(?i)\brm\s+(-\w*[rf]|--recursive|--force)|\brmdir\b|\brd\s+/s|\bdel\s+/[sq]"
                r"|\bremove-item\b[^;&|]*-(recurse|force)|\bformat(-volume)?\s|\bmkfs|\bdd\s+if="
                r"|\bdiskpart\b|\bclear-disk\b"), "Deletes files or disks — can't be undone."),
    (re.compile(r"(?i)\b(shutdown|reboot|halt|poweroff|restart-computer|stop-computer)\b"),
     "Shuts down or restarts this machine."),
    (re.compile(r"(?i)\bgit\s+(reset\s+--hard|clean\s+-\w*f|push\s+[^;&|]*(--force|-f\b)|checkout\s+--\s)"),
     "Discards git history or local changes."),
    (re.compile(r"(?i)(curl|wget|iwr|invoke-webrequest|irm|invoke-restmethod)\b[^;&]*\|\s*(sh|bash|zsh|python\w*|iex|invoke-expression)\b"
                r"|\b(iex|invoke-expression)\b"), "Downloads and runs code from the internet."),
    (re.compile(r"(?i)\b(chmod|chown|icacls|takeown)\b[^;&|]*(-R\b|/t\b)|\bset-executionpolicy\b"
                r"|\breg\s+(add|delete)\b|\b(set|remove)-itemproperty\b[^;&|]*hk(lm|cu):"),
     "Changes permissions or system settings."),
    (re.compile(r"(?i)\b(kill|pkill|killall|taskkill|stop-process)\b"), "Stops running programs."),
]
_ELEVATED = re.compile(r"(?i)(^|[\s;&|])(sudo|doas|runas)\b|-verb\s+runas")
_INSTALL = re.compile(
    r"(?i)\b(pip3?|pipx|uv|npm|pnpm|yarn|gem|cargo|go)\s+(install|i|add|uninstall|remove)\b"
    r"|\b(winget|choco|scoop|brew|apt(-get)?|dnf|yum|pacman|zypper|snap)\s+(install|upgrade|remove|uninstall)\b"
    r"|\b(install|uninstall|update)-(module|package|script)\b")


def check_shell(command: str) -> None:
    """LocalToolError if Sage may not even ask to run this command."""
    text = (command or "").strip()
    if not text:
        raise LocalToolError("Give a command to run.")
    if _SAGEX_IN_SHELL.search(text):
        raise LocalToolError("Use sagex_read / sagex_change for sagex commands, not the shell — "
                             "those are checked and shown properly.")
    if _ENV_DUMP.search(text):
        raise LocalToolError("Sage can't print environment variables — they may hold secrets. "
                             "Ask for one specific, non-secret variable instead.")
    for word in re.split(r"[\s;&|<>()'\"`,]+", text):
        name = os.path.basename(word.replace("\\", "/"))
        if name and is_secret_name(name):
            raise LocalToolError(f"That command touches {name}, which usually holds secrets; "
                                 "the user can run it directly with `!`.")
    if is_interactive(text):
        raise LocalToolError("That program needs an interactive terminal, which Sage can't use. "
                             "Use a non-interactive form, or give the user the command to run with `!`.")


@dataclass(frozen=True)
class ShellRisk:
    warning: str | None = None
    always_ask: bool = False


def shell_risk(command: str) -> ShellRisk:
    """The approval card's warning for a command — from here, never from Sage."""
    for pattern, text in _DESTRUCTIVE:
        if pattern.search(command):
            return ShellRisk(text, always_ask=True)
    if _ELEVATED.search(command):
        return ShellRisk("Runs with administrator rights. A password prompt can't be answered "
                         "here, so it may fail.", always_ask=True)
    if _INSTALL.search(command):
        return ShellRisk("Installs or removes software on this machine.")
    return ShellRisk()


def shell_env(environ: dict[str, str]) -> dict[str, str]:
    """The environment a Sage shell command gets: no SageCode key (its output
    goes to the model), no live-graph event lines."""
    env = {k: v for k, v in environ.items()
           if not k.upper().startswith("SAGEX_API_KEY") and k.upper() != "SAGEX_EVENTS"}
    env.update({"NO_COLOR": "1", "TERM": "dumb", "PYTHONIOENCODING": "utf-8",
                "GIT_TERMINAL_PROMPT": "0", "DEBIAN_FRONTEND": "noninteractive"})
    return env
