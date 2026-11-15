"""The Sage client against a real local WebSocket server (no Autosage involved)."""

import asyncio
import json

import pytest
from websockets.asyncio.server import serve

from sagex import config, sage
from sagex.api import store

KEY = "sgx_" + "t" * 43


@pytest.fixture(autouse=True)
def _signed_in(monkeypatch):
    monkeypatch.setattr(store, "get_key", lambda: KEY)


def _run(monkeypatch, handler, scenario):
    """Start `handler` on a free port, point sagex at it, run `scenario(session)`."""
    async def main():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(config, "ai_url", lambda: f"http://127.0.0.1:{port}")
            session = sage.SageSession()
            try:
                return await scenario(session)
            finally:
                await session.close()
    return asyncio.run(main())


async def _collect(session, text, context=None):
    return [frame async for frame in session.turn(text, context)]


def test_handshake_and_streamed_turn(monkeypatch):
    seen = {}

    async def handler(ws):
        seen["path"] = ws.request.path
        seen["headers"] = dict(ws.request.headers)
        await ws.send(json.dumps({"type": "ready", "session_id": "s1"}))
        seen["turn"] = json.loads(await ws.recv())
        for piece in ("Hi", " there"):
            await ws.send(json.dumps({"type": "token", "text": piece}))
        await ws.send(json.dumps({"type": "done", "cancelled": False}))
        await ws.wait_closed()

    context = {"last_command": {"command": "ls", "exit_code": 0, "output": "a"}}
    frames = _run(monkeypatch, handler, lambda s: _collect(s, "hello", context))

    assert [f["type"] for f in frames] == ["token", "token", "done"]
    assert seen["path"] == "/sage/ws"
    headers = {k.lower(): v for k, v in seen["headers"].items()}
    assert headers["x-api-key"] == KEY
    assert headers["user-agent"].startswith("sagex/")
    assert len(headers["x-sagex-invocation"]) == 32
    assert "origin" not in headers                    # the server refuses browser origins
    assert seen["turn"] == {"type": "turn", "text": "hello", "context": context}


def test_refused_session_says_why(monkeypatch):
    async def handler(ws):
        await ws.send(json.dumps({"type": "error", "code": "unauthenticated", "fatal": True,
                                  "message": "This session was revoked. Run: sagex auth login"}))
        await ws.close(code=4401)

    async def scenario(session):
        with pytest.raises(sage.SageError) as exc:
            await _collect(session, "hi")
        return exc.value

    error = _run(monkeypatch, handler, scenario)
    assert error.fatal and "revoked" in error.message


def test_cancel_and_reconnect(monkeypatch):
    connections = []

    async def handler(ws):
        connections.append(ws)
        await ws.send(json.dumps({"type": "ready", "session_id": "s1"}))
        json.loads(await ws.recv())                      # the turn
        await ws.send(json.dumps({"type": "token", "text": "Part"}))
        assert json.loads(await ws.recv()) == {"type": "cancel"}
        await ws.send(json.dumps({"type": "done", "cancelled": True}))
        await ws.close(code=4408)                        # e.g. the idle close

    async def scenario(session):
        first = []
        async for frame in session.turn("long one"):
            first.append(frame)
            if frame["type"] == "token":
                await session.cancel()
        await asyncio.sleep(0.05)                        # let the close land
        second_start = await session.turn("again").__anext__()
        return first, second_start

    first, second_start = _run(monkeypatch, handler, scenario)
    assert first[-1] == {"type": "done", "cancelled": True}
    assert second_start == {"type": "reconnected"}       # the UI tells the user
    assert len(connections) == 2


def test_unreachable_server(monkeypatch):
    monkeypatch.setattr(config, "ai_url", lambda: "http://127.0.0.1:9")   # nothing listens

    async def scenario():
        with pytest.raises(sage.SageError) as exc:
            await _collect(sage.SageSession(), "hi")
        return exc.value

    assert asyncio.run(scenario()).message == sage.UNREACHABLE


def test_signed_out_never_connects(monkeypatch):
    monkeypatch.setattr(store, "get_key", lambda: None)

    async def scenario():
        with pytest.raises(sage.SageError) as exc:
            await _collect(sage.SageSession(), "hi")
        return exc.value

    assert asyncio.run(scenario()).message == sage.SIGN_IN


@pytest.mark.parametrize("url, expected", [
    ("https://api.example.com/api/ai", "wss://api.example.com/api/ai/sage/ws"),
    ("http://localhost:8080/api/ai", "ws://localhost:8080/api/ai/sage/ws"),
])
def test_socket_url(monkeypatch, url, expected):
    monkeypatch.setattr(config, "ai_url", lambda: url)
    assert sage.socket_url() == expected


# --- what the last command shares --------------------------------------------

@pytest.mark.parametrize("command", [
    "sagex show key prod-db --reveal", "env", "printenv", "Get-ChildItem env:", "set",
])
def test_secret_printing_commands_are_never_shared(command):
    assert sage.command_context(command, 0, "PASSWORD=hunter2") is None


def test_shared_output_is_scrubbed_and_tailed():
    output = "\n".join(f"line {i}" for i in range(200)) + (
        "\nDB_PASSWORD=hunter2\napi_key: abc123\nusing sgx_" + "x" * 43)
    ctx = sage.command_context("python deploy.py", 1, output)
    assert ctx["exit_code"] == 1 and ctx["command"] == "python deploy.py"
    assert "line 0\n" not in ctx["output"] and "line 199" in ctx["output"]
    for secret in ("hunter2", "abc123", "x" * 43):
        assert secret not in ctx["output"]
    assert "DB_PASSWORD=*****" in ctx["output"]
