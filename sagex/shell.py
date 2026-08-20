"""Native shell command execution with a persistent session.

A ShellSession remembers two things between commands:
  - which shell to use (PowerShell / cmd / bash / sh)
  - the current working directory (so `cd` persists)

Kept separate from the UI: the app decides WHAT to show, this decides HOW to run.
Blocking on purpose — the app calls it from a background thread.
"""

import os
import shutil
import subprocess


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


def available_shells() -> list[str]:
    """Return the shells actually installed, in preference order."""
    found = [name for name in _CANDIDATES if shutil.which(_SHELL_EXES[name])]
    if not found:
        found = ["cmd"] if os.name == "nt" else ["sh"]
    return found


class ShellSession:
    """Runs commands in a chosen shell, remembering the working directory."""

    def __init__(self) -> None:
        self.shells = available_shells()
        self.shell = self.shells[0]
        self.cwd = os.getcwd()

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

    def run(self, command: str) -> tuple[str, int]:
        """Run a command, returning (combined_output, exit_code)."""
        stripped = command.strip()

        # Handle a plain `cd` ourselves so the directory persists across commands.
        is_plain_cd = stripped == "cd" or stripped.startswith("cd ")
        if is_plain_cd and not any(op in stripped for op in _CHAIN_OPS):
            return self._change_dir(stripped)

        result = subprocess.run(
            self._invocation(command),
            cwd=self.cwd,               # run in our tracked directory
            capture_output=True,
            text=True,
        )
        output = (result.stdout + result.stderr).rstrip()
        return output, result.returncode

    def _invocation(self, command: str) -> list[str]:
        """Build the argv list that runs `command` in the chosen shell."""
        if self.shell == "cmd":
            return ["cmd.exe", "/c", command]
        if self.shell in ("powershell", "pwsh"):
            return [_SHELL_EXES[self.shell], "-NoProfile", "-Command", command]
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
