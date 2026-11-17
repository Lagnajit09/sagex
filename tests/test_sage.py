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


def _run(monkeypatch, handler, scenario, hello=None):
    """Start `handler` on a free port, point sagex at it, run `scenario(session)`."""
    async def main():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(config, "ai_url", lambda: f"http://127.0.0.1:{port}")
            session = sage.SageSession(hello=hello)
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


HELLO = {"index": "Read-only — run these with sagex_read:\nlist run", "tools": ["sagex_read"]}


def test_hello_offers_tools_and_tool_results_flow(monkeypatch):
    seen = {}

    async def handler(ws):
        await ws.send(json.dumps({"type": "ready", "session_id": "s1", "features": ["tools"]}))
        seen["hello"] = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "hello", "tools": ["sagex_read"]}))
        seen["turn"] = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "tool_call", "id": "t1", "name": "sagex_read",
                                  "args": ["list", "run"]}))
        seen["result"] = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "done", "cancelled": False}))
        await ws.wait_closed()

    async def scenario(session):
        frames = []
        async for frame in session.turn("runs?"):
            frames.append(frame)
            if frame["type"] == "tool_call":
                await session.send_tool_result({"type": "tool_result", "id": frame["id"],
                                                "exit_code": 0, "output": "r1"})
        return frames, session.tools

    frames, tools = _run(monkeypatch, handler, scenario, hello=lambda: HELLO)
    assert seen["hello"] == {"type": "hello", **HELLO}
    assert seen["turn"] == {"type": "turn", "text": "runs?"}
    assert seen["result"] == {"type": "tool_result", "id": "t1", "exit_code": 0, "output": "r1"}
    assert [f["type"] for f in frames] == ["tool_call", "done"] and tools == ["sagex_read"]


def test_no_hello_to_a_server_without_tools(monkeypatch):
    seen = {}

    async def handler(ws):
        await ws.send(json.dumps({"type": "ready", "session_id": "s1"}))   # an older server
        seen["first"] = json.loads(await ws.recv())
        await ws.send(json.dumps({"type": "done", "cancelled": False}))
        await ws.wait_closed()

    _run(monkeypatch, handler, lambda s: _collect(s, "hi"), hello=lambda: HELLO)
    assert seen["first"]["type"] == "turn"


def test_server_notice_about_the_index_is_shown_once(monkeypatch):
    async def handler(ws):
        await ws.send(json.dumps({"type": "ready", "session_id": "s1", "features": ["tools"]}))
        await ws.recv()
        await ws.send(json.dumps({"type": "hello", "tools": [], "message": "can't use the list"}))
        for _ in range(2):
            await ws.recv()
            await ws.send(json.dumps({"type": "done", "cancelled": False}))
        await ws.wait_closed()

    async def scenario(session):
        return await _collect(session, "a"), await _collect(session, "b")

    first, second = _run(monkeypatch, handler, scenario, hello=lambda: HELLO)
    assert first[0] == {"type": "notice", "message": "can't use the list"}
    assert [f["type"] for f in second] == ["done"]


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
