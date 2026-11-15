"""Headless TUI tests: plain text goes to Sage (faked here — no network)."""

import asyncio

from sagex import config
from sagex.api import store
from sagex.app import SagexApp


def _isolate(monkeypatch, tmp_path, key):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(store, "get_key", lambda: key)


async def _settle(pilot, app, timeout=5.0):
    """Wait until the Sage turn (or command) finishes and the prompt is back."""
    for _ in range(int(timeout / 0.05)):
        await pilot.pause(0.05)
        if not app._busy:
            await pilot.pause()
            return
    raise AssertionError("the app stayed busy")


class FakeSage:
    """Stands in for SageSession: records turns, replays scripted frames."""

    def __init__(self, frames):
        self.frames = frames
        self.turns = []
        self.closed = False

    async def turn(self, text, context=None):
        self.turns.append((text, context))
        for frame in self.frames:
            yield frame

    async def cancel(self):
        pass

    async def close(self):
        self.closed = True


def test_plain_text_needs_sign_in(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, None)    # local env: no server calls

    async def scenario():
        app = SagexApp()
        async with app.run_test() as pilot:
            await pilot.press(*"hello", "enter")
            await pilot.pause()
            notices = list(app.query("ChatMessage.notice"))
            assert len(notices) == 1 and "sagex auth login" in notices[0].body
            assert not list(app.query("ChatMessage.sage"))   # no pretend reply

    asyncio.run(scenario())


def test_sage_reply_streams_into_the_transcript(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, "sgx_test")
    monkeypatch.setattr(SagexApp, "_start_loaders", lambda self: None)   # no resource fetches
    fake = FakeSage([
        {"type": "token", "text": "Run "},
        {"type": "token", "text": "`!sagex list workflow`."},
        {"type": "done", "cancelled": False, "model": "groq/m1", "usage": {}},
    ])

    async def scenario():
        app = SagexApp()
        app._sage = fake
        async with app.run_test() as pilot:
            await pilot.press(*"what workflows", "enter")
            await _settle(pilot, app)
            (reply,) = list(app.query("ChatMessage.sage"))
            assert reply.body == "Run `!sagex list workflow`."
            assert not app._busy and app.query_one("#prompt-row").display   # prompt is back
        assert fake.turns == [("what workflows", None)]
        assert fake.closed                                                 # socket closed on exit

    asyncio.run(scenario())


def test_sage_errors_show_as_notices(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, "sgx_test")
    monkeypatch.setattr(SagexApp, "_start_loaders", lambda self: None)
    fake = FakeSage([{"type": "error", "code": "admin_quota_exhausted", "fatal": False,
                      "message": "You've used today's 10 free AI replies."}])

    async def scenario():
        app = SagexApp()
        app._sage = fake
        async with app.run_test() as pilot:
            await pilot.press(*"hi", "enter")
            await _settle(pilot, app)
            notices = [n.body for n in app.query("ChatMessage.notice")]
            assert notices == ["You've used today's 10 free AI replies."]

    asyncio.run(scenario())


def test_last_command_becomes_context_once(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path, "sgx_test")
    monkeypatch.setattr(SagexApp, "_start_loaders", lambda self: None)
    fake = FakeSage([{"type": "done", "cancelled": False}])

    async def scenario():
        app = SagexApp()
        app._sage = fake
        async with app.run_test() as pilot:
            out = app._new_output_message()
            out.append_line("ERROR: connection refused")
            app._finish_command(out, 1, "sagex run workflow deploy")
            for question in ("why?", "and now?"):
                await pilot.press(*question, "enter")
                await _settle(pilot, app)
        (first_text, first_ctx), (_, second_ctx) = fake.turns
        assert first_ctx == {"last_command": {"command": "sagex run workflow deploy",
                                              "exit_code": 1,
                                              "output": "ERROR: connection refused"}}
        assert second_ctx is None

    asyncio.run(scenario())
