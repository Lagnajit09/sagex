"""Sage — the SageCode agent, over one WebSocket per conversation.

The socket lives at `<ai_url>/sage/ws` and authenticates with this session's API
key (the same headers every sagex request sends). Protocol, in short (full
version: autobot's sage/conversation.py):

    → hello{index, tools, conversation?} (once)  · turn{text, context}  · tool_result{id, …}  · cancel
    ← ready{features} · hello{tools, conversation?, message?} · token{text}
      · tool_call{id, name, args} · notice{message}
      · done{cancelled, model, usage, limit_reached, conversation_id?} · error{code, message, fatal}

A `tool_call` is answered by the app (sage_tools decides whether it may run)
with `send_tool_result` while `turn()` waits for the next frame.

Conversations are saved on the server (a "sessions" feature in `ready`). The
id arrives on `done`; reconnecting after a drop resumes the same conversation,
`open()` resumes an earlier one (/sessions) and `new()` starts afresh.

Async on purpose: the TUI runs it as a Textual worker on the app's own loop, so
tokens land in the transcript without thread hops.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable

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


def scrub(text: str) -> str:
    """Mask obvious secrets (SageCode keys, `password=…`-style values) in output."""
    text = _API_KEY.sub("sgx_*****", text)
    return _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}*****", text)


def command_context(command: str, exit_code: int, output: str) -> dict | None:
    """The `last_command` context for Sage, or None when it shouldn't be shared.

    Output is tailed and scrubbed of obvious secrets first; commands that print
    secrets by design (--reveal, env dumps) are never shared at all.
    """
    command = (command or "").strip()
    if not command or _NEVER_CONTEXT.search(command):
        return None
    tail = "\n".join((output or "").splitlines()[-_CONTEXT_LINES:])[-_CONTEXT_CHARS:]
    return {"command": command[:500], "exit_code": exit_code, "output": scrub(tail)}


class SageSession:
    """A conversation with Sage. Connects lazily; reconnects after a drop.

    `hello` builds the frame that offers Sage this CLI's tools (command index +
    tool names); it's sent once per connection, only to a server whose `ready`
    lists the "tools" feature.
    """

    def __init__(self, hello: Callable[[], dict] | None = None) -> None:
        self._ws: ClientConnection | None = None
        self._ever_connected = False
        self._hello = hello
        self.tools: list[str] = []           # what the server accepted for this connection
        self.features: list[str] = []        # what the server said it supports (ready)
        self._notice: str | None = None      # e.g. the server couldn't use the index
        # The saved conversation this session is in (None: a new one starts
        # with the next message), and what the server said when it resumed one.
        self.conversation_id: str | None = None
        self.resumed: dict | None = None

    @property
    def saves_conversations(self) -> bool:
        return "sessions" in self.features

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

        self.tools, self._notice, self.resumed = [], None, None
        self.features = [f for f in first.get("features") or [] if isinstance(f, str)]
        if self._hello is not None and "tools" in self.features:
            hello = {"type": "hello", **self._hello()}
            if self.conversation_id and self.saves_conversations:
                hello["conversation"] = self.conversation_id
            try:
                await ws.send(json.dumps(hello))
                reply = json.loads(await asyncio.wait_for(ws.recv(), _OPEN_TIMEOUT))
            except (ConnectionClosed, TimeoutError, ValueError) as exc:
                await ws.close()
                raise SageError(UNREACHABLE) from exc
            if reply.get("type") == "hello":
                self.tools = [t for t in reply.get("tools") or [] if isinstance(t, str)]
                self._notice = reply.get("message")
                resumed = reply.get("conversation")
                if isinstance(resumed, dict) and resumed.get("id"):
                    self.resumed = resumed
                elif "conversation" in hello:
                    self.conversation_id = None      # gone: this connection starts a new one
        self._ws = ws

    async def open(self, conversation_id: str) -> dict:
        """Resume a saved conversation on a fresh connection. Returns the
        server's `conversation` (id, title, transcript) or raises SageError
        with what it said instead."""
        await self.close()
        self.conversation_id = conversation_id
        await self._connect()
        self._ever_connected = True
        if self.resumed is None:
            raise SageError(self._notice or "Sage couldn't open that conversation.")
        return self.resumed

    async def new(self) -> None:
        """Start a fresh conversation with the next message."""
        await self.close()
        self.conversation_id = None
        self.resumed = None
        self._ever_connected = False

    async def turn(self, text: str, context: dict | None = None):
        """Send one message; yield the server's frames until `done` or `error`.

        Yields {"type": "reconnected", "resumed": bool} first when the earlier
        connection ended (idle/drop): resumed means the server picked the saved
        conversation back up; otherwise Sage won't remember it.
        """
        if not self.connected:
            reconnecting = self._ever_connected
            await self._connect()
            self._ever_connected = True
            if reconnecting:
                yield {"type": "reconnected", "resumed": self.resumed is not None}
            if self._notice:
                yield {"type": "notice", "message": self._notice}

        frame: dict = {"type": "turn", "text": text}
        if context:
            frame["context"] = context
        try:
            await self._ws.send(json.dumps(frame))
            while True:
                message = json.loads(await self._ws.recv())
                if message.get("type") == "done" and isinstance(message.get("conversation_id"), str):
                    self.conversation_id = message["conversation_id"]
                yield message
                if message.get("type") in ("done", "error"):
                    return
        except ConnectionClosed as exc:
            self._ws = None
            raise SageError("Lost the connection to Sage mid-reply. Try again.") from exc

    async def send_tool_result(self, frame: dict) -> None:
        """Answer a `tool_call` (sent while `turn()` is waiting for the next frame)."""
        if self.connected:
            try:
                await self._ws.send(json.dumps(frame))
            except ConnectionClosed:
                self._ws = None              # turn() notices on its next recv

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
