"""Which local file a command picks. The workspace is the one place local
resources live: relative paths resolve there, never in the current folder."""

import os

import pytest

from sagex import config, pusher


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """A workspace with scripts/report.sh, and a separate current folder."""
    workspace = tmp_path / "ws"
    (workspace / "scripts").mkdir(parents=True)
    (workspace / "scripts" / "report.sh").write_text("echo real\n")
    here = tmp_path / "here"
    here.mkdir()
    monkeypatch.chdir(here)
    monkeypatch.delenv(config.WORKSPACE_ONLY_ENV, raising=False)
    return workspace


def test_bare_name_finds_the_workspace_script(ws):
    assert pusher.resolve_script_file(ws, "report.sh").samefile(ws / "scripts" / "report.sh")
    assert pusher.resolve_script_file(ws, "report").samefile(ws / "scripts" / "report.sh")


def test_a_relative_path_is_under_the_workspace_from_any_folder(ws):
    assert pusher.resolve_script_file(ws, "scripts/report.sh").samefile(ws / "scripts" / "report.sh")


def test_a_copy_in_the_current_folder_is_ignored(ws):
    open("report.sh", "w").write("# template\n")          # e.g. a stray `new script` template
    assert pusher.resolve_script_file(ws, "report.sh").samefile(ws / "scripts" / "report.sh")


def test_two_different_workspace_files_are_never_picked_silently(ws):
    (ws / "report.sh").write_text("# other\n")
    with pytest.raises(pusher.ScriptFileNotFound) as exc:
        pusher.resolve_script_file(ws, "report.sh")
    assert {os.path.normcase(str(p)) for p in exc.value.matches} == {
        os.path.normcase(str((ws / "report.sh").resolve())),
        os.path.normcase(str((ws / "scripts" / "report.sh").resolve()))}


def test_an_absolute_path_is_used_as_given(ws, tmp_path):
    other = tmp_path / "other.sh"
    other.write_text("echo other\n")
    assert pusher.resolve_script_file(ws, str(other)) == other


def test_missing_file_lists_where_it_looked(ws):
    with pytest.raises(pusher.ScriptFileNotFound) as exc:
        pusher.resolve_script_file(ws, "nope.sh")
    assert not exc.value.matches
    assert ws / "scripts" / "nope.sh" in exc.value.looked
    assert not any(os.path.samefile(p.parent, os.getcwd()) for p in exc.value.looked
                   if p.parent.exists())


def test_workflow_paths_are_under_the_workspace(ws):
    (ws / "workflows").mkdir()
    (ws / "workflows" / "deploy.json").write_text("{}")
    open("deploy.json", "w").write("{}")                    # ignored: not the workspace
    assert pusher.resolve_workflow_file(ws, "deploy").samefile(ws / "workflows" / "deploy.json")
    assert pusher.resolve_workflow_file(ws, "workflows/deploy.json").samefile(
        ws / "workflows" / "deploy.json")


def test_output_paths_are_under_the_workspace(ws):
    assert config.workspace_path("scripts/new.sh", ws) == ws / "scripts" / "new.sh"


@pytest.mark.parametrize("ref", ["../outside.sh", "~/x.sh"])
def test_sage_runs_cant_leave_the_workspace(ws, monkeypatch, ref):
    monkeypatch.setenv(config.WORKSPACE_ONLY_ENV, "1")
    with pytest.raises(config.OutsideWorkspace):
        pusher.resolve_script_file(ws, ref)


def test_sage_runs_cant_use_an_absolute_path_elsewhere(ws, monkeypatch, tmp_path):
    other = tmp_path / "other.sh"
    other.write_text("echo other\n")
    monkeypatch.setenv(config.WORKSPACE_ONLY_ENV, "1")
    with pytest.raises(config.OutsideWorkspace):
        pusher.resolve_script_file(ws, str(other))
    with pytest.raises(config.OutsideWorkspace, match="secrets"):
        config.workspace_path(".env", ws)
    assert pusher.resolve_script_file(ws, "scripts/report.sh").samefile(ws / "scripts" / "report.sh")
