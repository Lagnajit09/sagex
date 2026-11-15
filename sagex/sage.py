"""Sage — the SageCode agent, over one WebSocket per conversation.

The socket lives at `<ai_url>/sage/ws` and authenticates with this session's API
key (the same headers every sagex request sends). Protocol, in short (full
version: autobot's sage/conversation.py):

    → {"type": "turn", "text": ..., "context": {...}}     → {"type": "cancel"}
    ← ready · token{text} · done{cancelled, model, usage} · error{code, message, fatal}

Async on purpose: the TUI runs it as a Textual worker on the app's own loop, so
tokens land in the transcript without thread hops.
"""

from __future__ import annotations

import asyncio
import json
import re

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.protocol import State

from sagex import config
from sagex.api import build_client, store

_OPEN_TIMEOUT = 20.0          # the server checks the key with Autosage during the handshake
_MAX_FRAME = 2 ** 20

UNREACHABLE = "Can't reach Sage right now. Check your connection and try again."
SIGN_IN = "Sign in to talk to Sage: run !sagex auth login"

# Last-command context: how much output travels with the next question.
_CONTEXT_LINES = 80
_CONTEXT_CHARS = 4000
# Commands whose output may contain secrets never become context.
_NEVER_CONTEXT = re.compile(r"--reveal\b|\b(printenv|env|set)\s*$|\benv:|\bauth\s+(login|token)",
                            re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([\w.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key)[\w.-]*)"
    r"(\s*[:=]\s*)(\S+)"
)
_API_KEY = re.compile(r"sgx_[A-Za-z0-9_-]+")


class SageError(Exception):
    """A turn that couldn't run; `message` is ready to show."""

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.fatal = fatal


def socket_url() -> str:
    base = config.ai_url()
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/sage/ws"


def command_context(command: str, exit_code: int, output: str) -> dict | None:
    """The `last_command` context for Sage, or None when it shouldn't be shared.

    Output is tailed and scrubbed of obvious secrets first; commands that print
    secrets by design (--reveal, env dumps) are never shared at all.
    """
    command = (command or "").strip()
    if not command or _NEVER_CONTEXT.search(command):
        return None
    tail = "\n".join((output or "").splitlines()[-_CONTEXT_LINES:])[-_CONTEXT_CHARS:]
    tail = _API_KEY.sub("sgx_*****", tail)
    tail = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}*****", tail)
    return {"command": command[:500], "exit_code": exit_code, "output": tail}


class SageSession:
    """A conversation with Sage. Connects lazily; reconnects after a drop."""

    def __init__(self) -> None:
        self._ws: ClientConnection | None = None
        self._ever_connected = False

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ws.state is State.OPEN

    async def _connect(self) -> None:
        if not store.get_key():
            raise SageError(SIGN_IN, fatal=True)
        headers = build_client().identity_headers()
        headers.pop("Accept", None)
        try:
            ws = await connect(
                socket_url(),
                additional_headers=headers,
                user_agent_header=None,          # ours (sagex/<ver> (<os>)) is in `headers`
                open_timeout=_OPEN_TIMEOUT,
                max_size=_MAX_FRAME,
            )
        except InvalidStatus as exc:
            status = exc.response.status_code
            if status in (502, 503, 504):
                raise SageError("Sage is temporarily unavailable. Please try again shortly.") from exc
            raise SageError(UNREACHABLE) from exc
        except (OSError, TimeoutError, WebSocketException) as exc:
            raise SageError(UNREACHABLE) from exc

        try:
            first = json.loads(await asyncio.wait_for(ws.recv(), _OPEN_TIMEOUT))
        except (ConnectionClosed, TimeoutError, ValueError) as exc:
            await ws.close()
            raise SageError(UNREACHABLE) from exc
        if first.get("type") != "ready":
            await ws.close()
            raise SageError(first.get("message") or UNREACHABLE, fatal=bool(first.get("fatal")))
        self._ws = ws

    async def turn(self, text: str, context: dict | None = None):
        """Send one message; yield the server's frames until `done` or `error`.

        Yields {"type": "reconnected"} first when an earlier conversation ended
        (idle/drop) — Sage won't remember it, so the UI should say so.
        """
        if not self.connected:
            reconnecting = self._ever_connected
            await self._connect()
            self._ever_connected = True
            if reconnecting:
                yield {"type": "reconnected"}

        frame: dict = {"type": "turn", "text": text}
        if context:
            frame["context"] = context
        try:
            await self._ws.send(json.dumps(frame))
            while True:
                message = json.loads(await self._ws.recv())
                yield message
                if message.get("type") in ("done", "error"):
                    return
        except ConnectionClosed as exc:
            self._ws = None
            raise SageError("Lost the connection to Sage mid-reply. Try again.") from exc

    async def cancel(self) -> None:
        """Ask Sage to stop the current reply (it answers with done{cancelled})."""
        if self.connected:
            try:
                await self._ws.send(json.dumps({"type": "cancel"}))
            except ConnectionClosed:
                self._ws = None

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
