"""What this machine tells Autosage about itself.

Sent as the User-Agent (`sagex/1.1.0 (Windows 11)`) and when signing in, and shown
on the user's Autosage profile so they can recognise their SageCode sessions.
Nothing here is secret or used for authorization.
"""

import functools
import platform
import sys

from sagex import __version__


@functools.cache
def os_name() -> str:
    """A short, readable OS name like 'Windows 11', 'macOS 15.1' or 'Ubuntu 24.04'."""
    system = platform.system()
    if system == "Windows":
        release = platform.release()
        # Python < 3.12 reports Windows 11 as release '10'; its builds start at 22000.
        if release == "10" and sys.getwindowsversion().build >= 22000:
            release = "11"
        name = f"Windows {release}"
    elif system == "Darwin":
        name = f"macOS {platform.mac_ver()[0]}"
    elif system == "Linux":
        try:
            info = platform.freedesktop_os_release()
            name = f"{info.get('NAME', 'Linux')} {info.get('VERSION_ID', '')}"
        except OSError:
            name = "Linux"
    else:
        name = system or "Unknown OS"
    # Parentheses would break the User-Agent's '(…)' comment.
    return " ".join(name.replace("(", "").replace(")", "").split())[:100]


@functools.cache
def device_name() -> str:
    """This machine's hostname, used as the session's name."""
    return platform.node().strip()[:100]


@functools.cache
def user_agent() -> str:
    return f"sagex/{__version__} ({os_name()})"
