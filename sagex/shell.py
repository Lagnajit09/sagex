"""Native shell command execution with a persistent session.

A ShellSession remembers two things between commands:
  - which shell to use (PowerShell / cmd / bash / sh)
  - the current working directory (so `cd` persists)

Kept separate from the UI: the app decides WHAT to show, this decides HOW to run.
Blocking on purpose — the app calls it from a background thread.
"""

import os
import re
import shutil
import signal
import subprocess
from collections.abc import Callable


# Friendly name -> the executable to look for on PATH.
_SHELL_EXES = {
    "powershell": "powershell.exe",
    "pwsh": "pwsh",
    "cmd": "cmd.exe",
    "bash": "bash",
    "sh": "sh",
}

# Preference order per platform; the first one actually installed is the default.
_CANDIDATES = ["powershell", "pwsh", "cmd"] if os.name == "nt" else ["bash", "sh"]

# If a "cd" line contains any of these, it's a compound command — let the shell
# handle it instead of intercepting it as a plain directory change.
_CHAIN_OPS = ("&", "|", ";")

# Matches ANSI escape sequences (colors, cursor moves, screen clears) so we can
# strip them out before displaying — otherwise they corrupt our own UI.
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Programs that always take over the whole screen — we can't host these in-app.
_FULLSCREEN = {
    "top", "htop", "btop", "atop", "glances",
    "vi", "vim", "nvim", "nano", "pico", "emacs", "micro", "helix", "hx",
    "less", "more", "man", "tmux", "screen", "watch",
    "lazygit", "lazydocker", "k9s", "ranger", "ncdu", "gdb", "lldb",
}
# REPLs that are interactive only when launched bare (no script / args).
_REPLS = {
    "python", "python3", "node", "irb", "ipython", "bpython",
    "psql", "mysql", "mongo", "mongosh", "redis-cli", "sqlite3",
    "ftp", "sftp", "telnet",
}


def _clean(text: str) -> str:
    """Remove ANSI escape codes and stray carriage returns from output text."""
    return _ANSI_RE.sub("", text).replace("\r", "")


def is_interactive(command: str) -> bool:
    """True if the command would need a full interactive terminal (which we can't host)."""
    tokens = command.strip().split()
    if not tokens:
        return False
    prog = os.path.basename(tokens[0]).lower()
    if prog.endswith(".exe"):
        prog = prog[:-4]
    if prog in _FULLSCREEN:
        return True
    return prog in _REPLS and len(tokens) == 1   # bare REPL, no script to run


def _wsl_available() -> bool:
    """True if wsl.exe exists AND at least one distro is installed."""
    if not shutil.which("wsl.exe"):
        return False
    try:
        result = subprocess.run(
            ["wsl.exe", "-l", "-q"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return False
    # `wsl -l -q` output can contain NUL bytes (UTF-16); strip them before checking.
    return result.returncode == 0 and bool(result.stdout.replace("\x00", "").strip())


def available_shells() -> list[str]:
    """Return the shells actually installed, in preference order."""
    found = [name for name in _CANDIDATES if shutil.which(_SHELL_EXES[name])]
    if os.name == "nt" and _wsl_available():
        found.append("wsl")             # run Linux commands via wsl.exe
    if not found:
        found = ["cmd"] if os.name == "nt" else ["sh"]
    return found


class ShellSession:
    """Runs commands in a chosen shell, remembering the working directory."""

    def __init__(self, preferred: str | None = None) -> None:
        self.shells = available_shells()
        # Use the remembered shell if it's still available, otherwise the default.
        self.shell = preferred if preferred in self.shells else self.shells[0]
        self.cwd = os.getcwd()
        self._proc: subprocess.Popen | None = None   # the currently running command

    @property
    def prompt(self) -> str:
        """A short 'shell · dir' label, with the home folder shown as ~."""
        home = os.path.expanduser("~")
        cwd = self.cwd
        if cwd == home or cwd.startswith(home + os.sep):
            cwd = "~" + cwd[len(home):]
        return f"{self.shell} · {cwd}"

    def cycle_shell(self) -> str:
        """Switch to the next installed shell and return its name."""
        i = self.shells.index(self.shell)
        self.shell = self.shells[(i + 1) % len(self.shells)]
        return self.shell

    def run_streaming(self, command: str, on_line: Callable[[str], None]) -> int:
        """Run a command, calling on_line(text) for each output line.

        Returns the exit code. `on_line` is called from the SAME thread this runs
        on; the caller is responsible for marshalling those lines onto the UI.
        """
        stripped = command.strip()

        # Full-screen / interactive programs need a real terminal — refuse politely
        # instead of hanging or scrambling the UI.
        if is_interactive(stripped):
            prog = stripped.split()[0]
            on_line(f"'{prog}' needs an interactive terminal, which sagex can't host yet.")
            on_line("Run it in a separate terminal window instead.")
            return 1

        # Handle a plain `cd` ourselves so the directory persists across commands.
        is_plain_cd = stripped == "cd" or stripped.startswith("cd ")
        if is_plain_cd and not any(op in stripped for op in _CHAIN_OPS):
            message, code = self._change_dir(stripped)
            on_line(message)
            return code

        # Start the command in its OWN process group / session so cancel() can
        # kill the whole tree (the shell AND whatever it spawned), not just the top.
        group_kwargs = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        proc = subprocess.Popen(
            self._invocation(command),
            cwd=self.cwd,               # run in our tracked directory
            stdin=subprocess.DEVNULL,   # feed EOF so commands that read stdin don't hang
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge stderr into the same stream
            text=True,
            bufsize=1,                  # line-buffered
            errors="replace",
            **group_kwargs,
        )
        self._proc = proc               # expose it so cancel() can reach it
        try:
            for line in proc.stdout:    # blocks until each line arrives, then loops
                on_line(_clean(line.rstrip("\n")))   # strip control codes before display
            proc.wait()
            return proc.returncode
        finally:
            self._proc = None           # command finished (or was killed)

    def is_running(self) -> bool:
        """True while a command is executing."""
        return self._proc is not None and self._proc.poll() is None

    def cancel(self) -> bool:
        """Kill the running command and its children. Returns True if one was running."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return False
        try:
            if os.name == "nt":
                # taskkill /T terminates the whole process tree (shell + children).
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)   # kill the group
        except Exception:
            try:
                proc.kill()                                       # last-resort fallback
            except Exception:
                pass
        return True

    def _invocation(self, command: str) -> list[str]:
        """Build the argv list that runs `command` in the chosen shell."""
        if self.shell == "cmd":
            return ["cmd.exe", "/c", command]
        if self.shell in ("powershell", "pwsh"):
            return [_SHELL_EXES[self.shell], "-NoProfile", "-Command", command]
        if self.shell == "wsl":
            return ["wsl.exe", "bash", "-c", command]     # Linux command in WSL
        return [_SHELL_EXES[self.shell], "-c", command]   # bash / sh

    def _change_dir(self, command: str) -> tuple[str, int]:
        """Update self.cwd for a `cd` command. Returns (message, exit_code)."""
        parts = command.split(maxsplit=1)
        target = parts[1].strip() if len(parts) > 1 else "~"   # bare `cd` -> home
        target = target.strip('"').strip("'")                  # drop surrounding quotes
        target = os.path.expanduser(os.path.expandvars(target))
        if not os.path.isabs(target):
            target = os.path.join(self.cwd, target)            # resolve relative to cwd
        target = os.path.normpath(target)

        if os.path.isdir(target):
            self.cwd = target
            return self.cwd, 0
        return f"cd: no such directory: {target}", 1
