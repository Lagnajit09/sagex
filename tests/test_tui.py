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
        self.conversation_id = None
        self.opened = []
        self.fresh = 0

    async def open(self, conversation_id):
        self.opened.append(conversation_id)
        self.conversation_id = conversation_id
        return {"id": conversation_id, "title": "cpu script", "transcript": [
            {"prompt": "write a cpu script", "reply": "Done: **scripts/cpu.sh**.", "cancelled": False}]}

    async def new(self):
        self.fresh += 1
        self.conversation_id = None

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
        ((text, context),) = fake.turns
        assert text == "what workflows" and set(context) == {"env"}      # where the tools act
        assert set(context["env"]) == {"os", "shell", "workspace"}       # never the cwd
        assert fake.closed                                                 # socket closed on exit

    asyncio.run(scenario())


def test_sessions_lists_reopens_and_deletes(monkeypatch, tmp_path):
    from sagex.app import resources
    _isolate(monkeypatch, tmp_path, "sgx_test")
    monkeypatch.setattr(SagexApp, "_start_loaders", lambda self: None)
    rows = [{"id": "c1", "title": "cpu script", "workspace": "infra", "turns": 4, "device": "laptop",
             "when": "2h ago"},
            {"id": "c2", "title": "deploy fix", "workspace": "", "turns": 1, "device": "", "when": "3d ago"}]
    deleted = []
    monkeypatch.setattr(resources, "list_sage_conversations", lambda client: list(rows))
    monkeypatch.setattr(resources, "delete_sage_conversation", lambda client, cid: deleted.append(cid))
    fake = FakeSage([])

    async def scenario():
        app = SagexApp()
        app._sage = fake
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"/sessions 1", "enter")              # nothing listed yet
            await pilot.pause()
            assert "Run /sessions first" in app.query("ChatMessage.notice").last().body
            await pilot.press(*"/sessions", "enter")
            await _settle(pilot, app)
            await pilot.pause(0.2)
            listing = app.query("ChatMessage.cmd_output").last().body
            assert "1. cpu script  (2h ago · laptop · infra · 4 messages)" in listing
            assert "2. deploy fix  (3d ago · 1 message)" in listing

            await pilot.press(*"/sessions 1", "enter")
            await _settle(pilot, app)
            assert fake.opened == ["c1"]
            assert [m.body for m in app.query("ChatMessage.user")] == ["write a cpu script"]
            assert [m.body for m in app.query("ChatMessage.sage")] == ["Done: **scripts/cpu.sh**."]
            assert "Reopened “cpu script”" in app.query("ChatMessage.notice").last().body

            await pilot.press(*"/sessions delete 1", "enter")       # the open one
            await pilot.pause(0.3)
            assert deleted == ["c1"] and fake.fresh == 1
            assert "starts a new conversation" in app.query("ChatMessage.notice").last().body

            await pilot.press(*"/new", "enter")
            await pilot.pause(0.2)
            assert fake.fresh == 2

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
        assert first_ctx["last_command"] == {"command": "sagex run workflow deploy",
                                             "exit_code": 1,
                                             "output": "ERROR: connection refused"}
        assert "last_command" not in second_ctx and "env" in second_ctx

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


# --- files and the shell (phase 1.4c) ---------------------------------------------------

def _local_app(monkeypatch, tmp_path, frames):
    """An app whose workspace (and terminal folder) is a fresh temp folder."""
    monkeypatch.setattr(tool_card_module, "ARM_SECONDS", 0)
    ws = tmp_path / "ws"
    (ws / "scripts").mkdir(parents=True)
    monkeypatch.chdir(ws)
    app, fake = _tool_app(monkeypatch, tmp_path, [*frames, {"type": "done", "cancelled": False}])
    # sagex's settings folder must not contain the workspace (Sage never touches it).
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "cfg" / "config.json")
    return app, fake, ws


def _write(path, content, call_id="t1"):
    return {"type": "tool_call", "id": call_id, "name": "write_file", "path": path,
            "content": content, "summary": f"Create {path}"}


def test_write_file_previews_asks_then_writes(monkeypatch, tmp_path):
    app, fake, ws = _local_app(monkeypatch, tmp_path,
                               [_write("scripts/cpu.sh", "#!/bin/bash\nuptime\n")])

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"make a cpu script", "enter")
            await _until(pilot, lambda: _asking(app))
            (card,) = _asking(app)
            assert card.command == "write scripts/cpu.sh  (new file, 2 lines)"
            assert card.lines == ["#!/bin/bash", "uptime"]            # the preview
            assert [k for k, _ in card._choices] == ["y", "a", "n"]
            assert not (ws / "scripts" / "cpu.sh").exists()           # nothing before the answer
            await pilot.press("y")
            await _settle(pilot, app)
            assert card.state == "ok"

    asyncio.run(scenario())
    assert (ws / "scripts" / "cpu.sh").read_bytes() == b"#!/bin/bash\nuptime\n"
    assert fake.results[0]["output"].startswith("Created scripts/cpu.sh")


def test_allowing_a_file_covers_rewrites_of_it(monkeypatch, tmp_path):
    app, fake, ws = _local_app(monkeypatch, tmp_path, [
        _write("scripts/a.sh", "echo 1\n", "t1"), _write("scripts/a.sh", "echo 2\n", "t2"),
        _write("scripts/b.sh", "echo b\n", "t3")])

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"write them", "enter")
            await _until(pilot, lambda: _asking(app))
            await pilot.press("a")
            await _until(pilot, lambda: len(list(app.query(ToolCard))) == 3 and _asking(app))
            first, second, third = list(app.query(ToolCard))
            assert second._note.startswith("allowed for this session") and second.diff
            assert third.state == "asking"                          # b.sh is another file: asks
            await pilot.press("n")
            await _settle(pilot, app)

    asyncio.run(scenario())
    assert (ws / "scripts" / "a.sh").read_bytes() == b"echo 2\n"
    assert not (ws / "scripts" / "b.sh").exists()
    assert "chose not to run" in fake.results[2]["error"]


def test_edit_file_shows_a_diff_and_shares_the_files_allowance(monkeypatch, tmp_path):
    app, fake, ws = _local_app(monkeypatch, tmp_path, [
        _write("scripts/a.sh", "echo one\necho two\n", "t1"),
        {"type": "tool_call", "id": "t2", "name": "edit_file", "path": "scripts/a.sh",
         "edits": [{"old_text": "echo two", "new_text": "echo 2"}], "summary": "Use a digit"},
        {"type": "tool_call", "id": "t3", "name": "edit_file", "path": "scripts/a.sh",
         "edits": [{"old_text": "echo three", "new_text": "x"}], "summary": "Nope"}])

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"write then fix", "enter")
            await _until(pilot, lambda: _asking(app))
            await pilot.press("a")                                     # allow writing a.sh
            await _settle(pilot, app)
            _, edit, bad = list(app.query(ToolCard))
            assert edit.command == "scripts/a.sh".join(("edit ", "  (1 change)"))
            assert edit.diff and "+echo 2" in edit.lines and edit._note.startswith("allowed")
            assert bad.state == "refused"

    asyncio.run(scenario())
    assert (ws / "scripts" / "a.sh").read_bytes() == b"echo one\necho 2\n"
    assert fake.results[1]["output"] == "Edited scripts/a.sh (1 change; now 2 lines)."
    assert "wasn't found" in fake.results[2]["error"]


def test_file_outside_the_workspace_is_refused_without_asking(monkeypatch, tmp_path):
    app, fake, ws = _local_app(monkeypatch, tmp_path, [
        {"type": "tool_call", "id": "t1", "name": "read_file", "path": "../secret.txt"},
        {"type": "tool_call", "id": "t2", "name": "read_file", "path": ".env"}])

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press(*"read them", "enter")
            await _settle(pilot, app)
            assert [c.state for c in app.query(ToolCard)] == ["refused", "refused"]

    asyncio.run(scenario())
    assert "outside the workspace" in fake.results[0]["error"]
    assert "secrets" in fake.results[1]["error"]


def test_read_file_shows_what_sage_would_see_then_sends_it(monkeypatch, tmp_path):
    app, fake, ws = _local_app(monkeypatch, tmp_path, [
        {"type": "tool_call", "id": "t1", "name": "read_file", "path": "scripts/a.sh"}])
    (ws / "scripts" / "a.sh").write_text("echo hi\n")

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"read a.sh", "enter")
            await _until(pilot, lambda: _asking(app))
            (card,) = _asking(app)
            assert card.lines == ["echo hi"] and card.command == "read scripts/a.sh"
            assert fake.results == []                                    # not sent before approval
            await pilot.press("y")
            await _settle(pilot, app)

    asyncio.run(scenario())
    assert "    1  echo hi" in fake.results[0]["output"]


def test_delete_file_always_asks(monkeypatch, tmp_path):
    app, fake, ws = _local_app(monkeypatch, tmp_path, [
        {"type": "tool_call", "id": "t1", "name": "delete_file", "path": "old.sh",
         "summary": "Delete old.sh"}])
    (ws / "old.sh").write_text("x\n")

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"delete old.sh", "enter")
            await _until(pilot, lambda: _asking(app))
            (card,) = _asking(app)
            assert [k for k, _ in card._choices] == ["y", "n"] and "Can't be undone" in card.warning
            await pilot.press("y")
            await _settle(pilot, app)

    asyncio.run(scenario())
    assert not (ws / "old.sh").exists() and fake.results[0]["output"] == "Deleted old.sh."


def test_shell_command_runs_in_the_users_shell_without_the_key(monkeypatch, tmp_path):
    ran = []

    def fake_run(self, on_line, on_event=None):
        ran.append((self.argv, self.env, self.cwd, on_event))
        self.lines.append("Python 3.12.1")
        on_line("Python 3.12.1")
        return 0

    monkeypatch.setattr(sage_tools.CommandRun, "run", fake_run)
    monkeypatch.setenv("SAGEX_API_KEY", "sgx_should_not_leak")
    app, fake, ws = _local_app(monkeypatch, tmp_path, [
        {"type": "tool_call", "id": "t1", "name": "run_shell", "command": "python --version",
         "summary": "Check the Python version"}])

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"python version?", "enter")
            await _until(pilot, lambda: _asking(app))
            (card,) = _asking(app)
            assert card.command == "python --version" and card.glyph.startswith(app.session.shell)
            await pilot.press("y")
            await _settle(pilot, app)

    asyncio.run(scenario())
    ((argv, env, cwd, on_event),) = ran
    assert argv == app.session.invocation("python --version")
    assert "SAGEX_API_KEY" not in env and on_event is None and cwd == str(ws)
    assert fake.results[0]["output"] == "Python 3.12.1"


def test_shell_cant_be_used_for_sagex_or_env_dumps(monkeypatch, tmp_path):
    monkeypatch.setattr(sage_tools.CommandRun, "run",
                        lambda self, on_line, on_event=None: (_ for _ in ()).throw(AssertionError("ran")))
    app, fake, ws = _local_app(monkeypatch, tmp_path, [
        {"type": "tool_call", "id": "t1", "name": "run_shell", "command": "sagex show key k --reveal"},
        {"type": "tool_call", "id": "t2", "name": "run_shell", "command": "Get-ChildItem env:"}])

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press(*"show it", "enter")
            await _settle(pilot, app)
            assert [c.state for c in app.query(ToolCard)] == ["refused", "refused"]

    asyncio.run(scenario())
    assert "sagex_read" in fake.results[0]["error"] and "environment" in fake.results[1]["error"]


def test_risky_shell_command_always_asks(monkeypatch, tmp_path):
    app, fake, ws = _local_app(monkeypatch, tmp_path, [
        {"type": "tool_call", "id": "t1", "name": "run_shell", "command": "rm -rf build",
         "summary": "Clean the build folder"}])

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press(*"clean up", "enter")
            await _until(pilot, lambda: _asking(app))
            (card,) = _asking(app)
            assert [k for k, _ in card._choices] == ["y", "n"] and "can't be undone" in card.warning
            await pilot.press("n")
            await _settle(pilot, app)

    asyncio.run(scenario())
    assert "chose not to run" in fake.results[0]["error"]
