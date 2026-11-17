"""Sage's tools, CLI side: the index, the read/change/never table, help, running."""

import sys
import threading
import time

import pytest

from sagex import sage_tools as t


# --- the index -------------------------------------------------------------------

def test_index_is_compact_and_split_by_kind():
    index = t.command_index()
    read, _, rest = index.partition("\n\n")
    assert read.startswith("Read-only")
    assert "show script <ref> [--no-content]" in read
    assert "list workflow|script|trigger|key|server|vault" in read
    assert "run workflow <ref>" in rest and "run workflow" not in read
    assert len(index) < 5000                       # ~1k tokens, sent once per connection


@pytest.mark.parametrize("hidden", ["auth login", "create key", "update key", "copy key",
                                    "trigger create", "trigger regenerate", "trigger poll",
                                    "--reveal", "--secret-file", "--json"])
def test_index_leaves_out_secret_commands_and_options(hidden):
    assert hidden not in t.command_index()


def test_hello_offers_the_index_and_tools():
    hello = t.hello_frame()
    assert hello["index"] == t.command_index()
    assert hello["tools"] == ["sagex_help", "sagex_read"]


# --- check ---------------------------------------------------------------------------

def test_read_only_commands_pass():
    check = t.check(["show", "script", "deploy.sh", "--no-content"])
    assert check == t.Check(("show", "script"), read_only=True)


def test_changes_parse_but_are_not_read_only():
    check = t.check(["delete", "vault", "prod"])
    assert check.error is None and not check.read_only


@pytest.mark.parametrize("words, needle", [
    (["auth", "login"], "isn't available to Sage"),
    (["create", "key", "k", "--vault", "v"], "isn't available to Sage"),
    (["show", "key", "k", "--reveal"], "--reveal isn't available"),
    (["trigger", "fire", "t", "--secret-file=s.txt"], "--secret-file isn't available"),
    (["show", "run", "--help"], "Use sagex_help"),
    (["run", "workflow", "x", "--params", "a=b"], "No such option: --params"),
    (["list", "run", "--limit", "many"], "not a valid int"),
    (["show", "script"], "Missing argument"),
    (["list"], "`list` needs one of"),
    (["lst", "run"], "Unknown command 'lst'"),
])
def test_refusals_explain_themselves(words, needle):
    check = t.check(words)
    assert check.error and needle in check.error and not check.read_only


def test_check_never_runs_the_command(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("ran something"))
    t.check(["show", "script", "x"])


# --- help ----------------------------------------------------------------------------

def test_help_has_options_and_says_how_to_run_it():
    text, error = t.help_text(["run", "workflow"])
    assert error is None
    assert text.startswith("sagex run workflow <ref>")
    assert "--param, -p PARAM…" in text and "repeatable" in text
    assert text.endswith("give the user the `!sagex …` command to run.")


def test_help_hides_blocked_options():
    text, _ = t.help_text(["show", "key"])
    assert "--reveal" not in text.split("\n\n", 1)[0]          # not in the signature
    assert "Options:" not in text                             # its only option is blocked
    assert text.endswith("Read-only: run it with sagex_read.")


def test_help_for_never_commands_is_refused():
    text, error = t.help_text(["auth", "login"])
    assert text is None and "isn't available to Sage" in error


# --- results -------------------------------------------------------------------------

def test_result_tail_keeps_the_end_and_masks_secrets():
    lines = [f"line {i}" for i in range(400)] + ["password=hunter2", "key sgx_abcdefghijklmnop"]
    text, cut = t.result_tail(lines)
    assert cut and text.splitlines()[-1] == "key sgx_*****"
    assert "password=*****" in text and "hunter2" not in text
    assert len(text.splitlines()) == t.RESULT_LINES


def _program(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_command_run_streams_lines_and_exit_code():
    run = t.CommandRun([])
    run.program = _program("import sys; print('one'); print('\\x1b[31mtwo\\x1b[0m'); sys.exit(3)")
    seen = []
    assert run.run(seen.append) == 3
    assert seen == ["one", "two"] == run.lines                  # ANSI codes stripped
    result = t.result_frame("t1", 3, run, 0.25)
    assert result == {"type": "tool_result", "id": "t1", "exit_code": 3, "output": "one\ntwo",
                      "truncated": False, "seconds": 0.2}


def test_command_run_is_non_interactive():
    run = t.CommandRun([])
    run.program = _program("input('Delete it? ')")           # a prompt reads EOF, no hang
    assert run.run(lambda _line: None) != 0


def test_command_run_times_out():
    run = t.CommandRun([], timeout=0.5)
    run.program = _program("import time; time.sleep(30)")
    started = time.monotonic()
    run.run(lambda _line: None)
    assert time.monotonic() - started < 10
    assert run.timed_out
    assert "stopped after" in t.result_frame("t1", -1, run, 0.5)["error"]


def test_command_run_can_be_cancelled_from_another_thread():
    run = t.CommandRun([])
    run.program = _program("import time; print('started', flush=True); time.sleep(30)")
    started = threading.Event()

    def on_line(_line):
        started.set()

    worker = threading.Thread(target=run.run, args=(on_line,))
    worker.start()
    assert started.wait(10)
    run.cancel()
    worker.join(10)
    assert not worker.is_alive() and run.cancelled and not run.timed_out
