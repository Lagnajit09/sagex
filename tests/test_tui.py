"""Headless TUI tests: plain text goes to Sage (faked here — no network)."""

import asyncio

from sagex import config, sage_tools
from sagex.api import store
from sagex.app import SagexApp
from sagex.widgets.message import ChatMessage
from sagex.widgets.tool_card import ToolCard


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
        self.results = []
        self.closed = False

    async def turn(self, text, context=None):
        self.turns.append((text, context))
        for frame in self.frames:
            yield frame

    async def send_tool_result(self, frame):
        self.results.append(frame)

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


# --- Sage's tool calls (cards) ---------------------------------------------------

def _tool_app(monkeypatch, tmp_path, frames):
    _isolate(monkeypatch, tmp_path, "sgx_test")
    monkeypatch.setattr(SagexApp, "_start_loaders", lambda self: None)
    fake = FakeSage(frames)
    app = SagexApp()
    app._sage = fake
    return app, fake


def test_read_tool_runs_and_shows_a_card(monkeypatch, tmp_path):
    ran = []

    def fake_run(self, on_line, on_event=None):
        ran.append(self.words)
        for line in ("deploy.sh · python", "import os"):
            self.lines.append(line)
            on_line(line)
        return 0

    monkeypatch.setattr(sage_tools.CommandRun, "run", fake_run)
    app, fake = _tool_app(monkeypatch, tmp_path, [
        {"type": "token", "text": "Let me look."},
        {"type": "tool_call", "id": "t1", "name": "sagex_read", "args": ["show", "script", "deploy.sh"]},
        {"type": "token", "text": "It deploys the app."},
        {"type": "done", "cancelled": False},
    ])

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"what does deploy.sh do", "enter")
            await _settle(pilot, app)
            (card,) = list(app.query(ToolCard))
            assert card.tool == "sagex_read" and card.command == "sagex show script deploy.sh"
            assert card.lines == ["deploy.sh · python", "import os"] and card.state == "ok"
            # Text before the tool, the card, then text after it — in that order.
            log = app.query_one("#chat-log")
            order = [type(w).__name__ for w in log.children
                     if isinstance(w, (ToolCard, ChatMessage)) and not w.has_class("user")]
            assert order == ["ChatMessage", "ToolCard", "ChatMessage"]
            assert [m.body for m in app.query("ChatMessage.sage")] == ["Let me look.",
                                                                       "It deploys the app."]

    asyncio.run(scenario())
    assert ran == [["show", "script", "deploy.sh"]]
    (result,) = fake.results
    assert result["id"] == "t1" and result["exit_code"] == 0
    assert result["output"] == "deploy.sh · python\nimport os" and result["truncated"] is False


def test_read_tool_refuses_a_change(monkeypatch, tmp_path):
    monkeypatch.setattr(sage_tools.CommandRun, "run",
                        lambda self, on_line, on_event=None: (_ for _ in ()).throw(AssertionError("ran")))
    app, fake = _tool_app(monkeypatch, tmp_path, [
        {"type": "tool_call", "id": "t1", "name": "sagex_read", "args": ["delete", "vault", "prod"]},
        {"type": "done", "cancelled": False},
    ])

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press(*"delete prod", "enter")
            await _settle(pilot, app)
            (card,) = list(app.query(ToolCard))
            assert card.state == "refused"

    asyncio.run(scenario())
    (result,) = fake.results
    assert "changes things" in result["error"] and "exit_code" not in result


def test_help_tool_returns_help_without_running_anything(monkeypatch, tmp_path):
    app, fake = _tool_app(monkeypatch, tmp_path, [
        {"type": "tool_call", "id": "t1", "name": "sagex_help", "args": ["run", "workflow"]},
        {"type": "done", "cancelled": False},
    ])

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press(*"how do I run one", "enter")
            await _settle(pilot, app)
            (card,) = list(app.query(ToolCard))
            assert card.tool == "sagex_help" and card.state == "ok"

    asyncio.run(scenario())
    (result,) = fake.results
    assert "--param" in result["output"] and result["exit_code"] == 0


# --- changes: approval cards (phase 1.4b) ---------------------------------------------

from sagex.widgets import tool_card as tool_card_module  # noqa: E402


async def _until(pilot, condition, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        await pilot.pause(0.05)
        if condition():
            return
    raise AssertionError("condition never became true")


def _asking(app):
    return [c for c in app.query(ToolCard) if c.state == "asking"]


def _change_app(monkeypatch, tmp_path, calls, ran):
    """An app whose fake Sage makes `calls` (word lists) via sagex_change."""
    monkeypatch.setattr(tool_card_module, "ARM_SECONDS", 0)

    def fake_run(self, on_line, on_event=None):
        ran.append(self.words)
        if on_event is not None and self.words[:2] == ["run", "workflow"]:
            on_event({"t": "start", "view": {"name": "deploy", "edges": [],
                                             "nodes": [{"id": "n1", "label": "Build", "kind": "script"}]}})
            on_event({"t": "node", "id": "n1", "status": "success"})
            on_event({"t": "run", "status": "success"})
        self.lines.append("✓ done")
        on_line("✓ done")
        return 0

    monkeypatch.setattr(sage_tools.CommandRun, "run", fake_run)
    frames = [{"type": "tool_call", "id": f"t{i}", "name": "sagex_change", "args": words,
               "summary": f"Sage says: {' '.join(words)}"}
              for i, words in enumerate(calls, 1)]
    return _tool_app(monkeypatch, tmp_path, [*frames, {"type": "done", "cancelled": False}])


def test_change_asks_then_runs_once_approved(monkeypatch, tmp_path):
    ran = []
    app, fake = _change_app(monkeypatch, tmp_path, [["run", "workflow", "deploy"]], ran)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"run deploy", "enter")
            await _until(pilot, lambda: _asking(app))
            (card,) = _asking(app)
            assert card.summary == "Sage says: run workflow deploy"
            assert card.command == "sagex run workflow deploy" and card.warning is None
            assert [k for k, _ in card._choices] == ["y", "a", "n"]
            assert ran == []                                   # nothing runs before the answer
            await pilot.press("y")
            await _settle(pilot, app)
            assert card.state == "ok" and card._note == "approved once"
            assert card._view is not None and card._view.status == "success"   # live graph in the card

    asyncio.run(scenario())
    assert ran == [["run", "workflow", "deploy"]]
    assert fake.results[0]["exit_code"] == 0


def test_declined_change_never_runs(monkeypatch, tmp_path):
    ran = []
    app, fake = _change_app(monkeypatch, tmp_path, [["rename", "vault", "a", "b"]], ran)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press(*"rename a", "enter")
            await _until(pilot, lambda: _asking(app))
            await pilot.press("n")
            await _settle(pilot, app)
            (card,) = list(app.query(ToolCard))
            assert card.state == "declined"

    asyncio.run(scenario())
    assert ran == []
    assert "chose not to run" in fake.results[0]["error"]


def test_delete_always_asks_and_runs_with_yes(monkeypatch, tmp_path):
    ran = []
    app, fake = _change_app(monkeypatch, tmp_path, [["delete", "vault", "staging"]], ran)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"delete staging", "enter")
            await _until(pilot, lambda: _asking(app))
            (card,) = _asking(app)
            assert [k for k, _ in card._choices] == ["y", "n"]   # no "allow for the session"
            assert card.command == "sagex delete vault staging --yes"
            assert "every server and credential" in card.warning
            await pilot.press("a")                             # not a choice here: ignored
            assert card.state == "asking"
            await pilot.press("y")
            await _settle(pilot, app)

    asyncio.run(scenario())
    assert ran == [["delete", "vault", "staging", "--yes"]]


def test_allowed_for_the_session_until_revoked(monkeypatch, tmp_path):
    ran = []
    app, fake = _change_app(monkeypatch, tmp_path, [["run", "workflow", "deploy"],
                                                    ["run", "workflow", "deploy", "-p", "v=2"]], ran)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"run it twice", "enter")
            await _until(pilot, lambda: _asking(app))
            await pilot.press("a")
            await _settle(pilot, app)
            first, second = list(app.query(ToolCard))
            assert second._note.startswith("allowed for this session")   # didn't ask again
            assert "Sage may run 1 without asking" in str(app.query_one("#prompt-status").render())

            await pilot.press(*"/permissions", "enter")
            await pilot.pause()
            listing = list(app.query("ChatMessage.cmd_output"))[-1].body
            assert "1. sagex run workflow deploy" in listing
            await pilot.press(*"/permissions revoke 1", "enter")
            await pilot.pause()
            assert len(app._allowances) == 0
            assert "without asking" not in str(app.query_one("#prompt-status").render())

    asyncio.run(scenario())
    assert ran == [["run", "workflow", "deploy"], ["run", "workflow", "deploy", "-p", "v=2"]]


def test_esc_on_an_approval_stops_without_running(monkeypatch, tmp_path):
    ran = []
    app, fake = _change_app(monkeypatch, tmp_path, [["rename", "vault", "a", "b"]], ran)
    cancelled = []

    async def cancel():
        cancelled.append(True)

    fake.cancel = cancel

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press(*"rename a", "enter")
            await _until(pilot, lambda: _asking(app))
            await pilot.press("escape")
            await _settle(pilot, app)
            (card,) = list(app.query(ToolCard))
            assert card.state == "declined" and card._note == "stopped"

    asyncio.run(scenario())
    assert ran == [] and cancelled == [True]
    assert "stopped this before it ran" in fake.results[0]["error"]


def test_keys_typed_ahead_dont_answer_a_new_card(monkeypatch, tmp_path):
    ran = []
    app, fake = _change_app(monkeypatch, tmp_path, [["rename", "vault", "a", "b"]], ran)
    monkeypatch.setattr(tool_card_module, "ARM_SECONDS", 30)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press(*"rename a", "enter")
            await _until(pilot, lambda: _asking(app))
            await pilot.press("y")                             # too soon after it appeared
            assert _asking(app)
            app.action_stop_command()
            await _settle(pilot, app)

    asyncio.run(scenario())
    assert ran == []


def test_unknown_slash_command(monkeypatch, tmp_path):
    app, fake = _tool_app(monkeypatch, tmp_path, [])

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press(*"/nope", "enter")
            await pilot.pause()
            assert "Unknown command /nope" in list(app.query("ChatMessage.notice"))[-1].body
            assert fake.turns == []                            # never sent to Sage

    asyncio.run(scenario())
