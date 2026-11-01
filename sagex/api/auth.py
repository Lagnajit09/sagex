"""Signing in to Autosage from the CLI, and the CLI's own session.

`sagex auth login` uses a device-authorization flow (like `gh auth login`), so it
works over SSH or on a server with no browser too:

  1. start_login()  asks the server for a one-time code and a link;
  2. the user opens the link (on any device), signs in to Autosage and approves
     after checking the code matches the terminal;
  3. wait_for_approval() polls until then and returns the new API key, which the
     command stores in the OS keychain.

The key is a *session*: it shows on the user's profile, where it can be revoked.
"""

import time
from dataclasses import dataclass

from sagex import __version__, device
from sagex.api.client import ApiClient, ApiError

START_PATH = "/api/users/cli/auth/start/"
TOKEN_PATH = "/api/users/cli/auth/token/"
CURRENT_PATH = "/api/users/cli/sessions/current/"


@dataclass
class LoginStart:
    device_code: str          # secret: only this process ever sees it
    user_code: str            # shown to the user, e.g. WDJB-MJHT
    verification_uri: str     # /cli-auth, for typing the code in by hand
    verification_uri_complete: str
    expires_in: int
    interval: int


@dataclass
class LoginResult:
    api_key: str
    email: str
    session_name: str


def start_login(client: ApiClient) -> LoginStart:
    """Open a sign-in request. `client` must not carry an API key."""
    data = client.post(START_PATH, json={
        "device_name": device.device_name(),
        "os": device.os_name(),
        "cli_version": __version__,
    })
    return LoginStart(
        device_code=data["device_code"],
        user_code=data["user_code"],
        verification_uri=data["verification_uri"],
        verification_uri_complete=data["verification_uri_complete"],
        expires_in=int(data.get("expires_in") or 600),
        interval=max(int(data.get("interval") or 5), 1),
    )


def wait_for_approval(client: ApiClient, start: LoginStart, sleep=time.sleep) -> LoginResult:
    """Poll until the user approves; return the new key.

    Raises ApiError if the request is denied, expires or is rejected. Brief network
    trouble or rate limiting just slows the polling down; the request's own expiry
    bounds the wait.
    """
    interval = start.interval
    deadline = time.monotonic() + start.expires_in
    while True:
        sleep(interval)
        try:
            data = client.post(TOKEN_PATH, json={"device_code": start.device_code})
        except ApiError as exc:
            if exc.status is None or exc.status == 429 or exc.status >= 500:
                if time.monotonic() > deadline:
                    raise ApiError("This sign-in request expired. Run: sagex auth login") from exc
                interval = min(interval + 5, 30)
                continue
            raise
        if isinstance(data, dict) and data.get("status") == "approved":
            user = data.get("user") or {}
            session = data.get("session") or {}
            return LoginResult(
                api_key=data["api_key"],
                email=user.get("email") or "",
                session_name=session.get("name") or device.device_name(),
            )
        if time.monotonic() > deadline:
            raise ApiError("This sign-in request expired. Run: sagex auth login")
