"""Run commands: rerun, watch, cancel, and where run logs are saved."""

import json

import pytest
from typer.testing import CliRunner

from sagex import __main__ as cli
from sagex import config, copier, sage_tools
from sagex.api import ApiError, resources

runner = CliRunner()

RUN_ID = "3f2a9c1e-0000-4000-8000-000000000001"
NEW_RUN_ID = "7b7b7b7b-0000-4000-8000-000000000002"


def _workflow(password_default: str = "") -> dict:
    return {
        "id": "wf-1",
        "name": "Deploy",
        "nodes": [
            {"id": "t1", "type": "trigger", "data": {"type": "manual", "label": "Start"}},
            {"id": "a1", "type": "action", "data": {
                "type": "script", "label": "Ship",
                "parameters": [
                    {"id": "p-env", "name": "ENV", "type": "string", "value": "staging", "sourceType": "manual"},
                    {"id": "p-tag", "name": "TAG", "type": "string", "value": "", "sourceType": "manual"},
                    {"id": "p-pw", "name": "DB_PASSWORD", "type": "password", "value": password_default,
                     "sourceType": "manual"},
                ],
            }},
        ],
        "edges": [{"source": "t1", "target": "a1"}],
    }


def _run(status: str = "failed", inputs: dict | None = None) -> dict:
    return {"id": RUN_ID, "workflow_id": "wf-1", "workflow_name": "Deploy", "status": status,
            "created_at": "2026-11-25T10:00:00+00:00", "started_at": "2026-11-25T10:00:01+00:00",
            "finished_at": None if status in ("queued", "running") else "2026-11-25T10:00:09+00:00",
            "inputs": inputs if inputs is not None else {"p-env": "prod", "p-tag": "v42", "p-pw": "*****"}}


@pytest.fixture
def api(monkeypatch, tmp_path):
    """A fake server: records what was sent; the live follow is stubbed out."""
    calls: dict = {"triggered": [], "cancelled": [], "followed": []}
    state = {"run": _run(), "workflow": _workflow(), "nodes": []}

    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "workspace_dir", lambda: str(tmp_path / "ws"))
    monkeypatch.setattr(cli, "_client_or_exit", lambda: object())
    monkeypatch.setattr(resources, "resolve_run", lambda client, ref: state["run"])
    monkeypatch.setattr(resources, "get_workflow_detail", lambda client, wid: state["workflow"])
    monkeypatch.setattr(resources, "get_run_nodes", lambda client, rid: state["nodes"])

    def trigger(client, workflow_id, payload):
        calls["triggered"].append((workflow_id, payload))
        return {"workflow_run_id": NEW_RUN_ID, "status": "queued"}

    def cancel(client, run_id):
        calls["cancelled"].append(run_id)

    def follow(client, run_id, view, workspace, **kwargs):
        calls["followed"].append({"run_id": run_id, "view": view, "workspace": workspace, **kwargs})
        return 0

    monkeypatch.setattr(resources, "trigger_workflow_run", trigger)
    monkeypatch.setattr(resources, "cancel_workflow_run", cancel)
    monkeypatch.setattr(cli, "_follow_workflow_run", follow)
    monkeypatch.delenv("SAGEX_EVENTS", raising=False)
    return state, calls


# --- rerun -----------------------------------------------------------------------

def test_rerun_inputs_never_resend_a_masked_secret():
    specs = cli.runner.workflow_params(_workflow()["nodes"])
    prior, ask_again, dropped = cli._rerun_inputs(
        specs, {"p-env": "prod", "p-pw": "*****", "p-gone": "x", "p-tag": 7})
    assert prior == {"p-env": "prod", "p-tag": 7}
    assert ask_again == {"p-pw"}
    assert dropped == 1


def test_rerun_sends_the_earlier_inputs_and_follows_the_new_run(api):
    state, calls = api
    state["workflow"] = _workflow(password_default="saved")
    result = runner.invoke(cli.app, ["rerun", "3f2a"])
    assert result.exit_code == 0, result.output
    (workflow_id, payload), = calls["triggered"]
    assert workflow_id == "wf-1"
    # The masked password is not sent back; the workflow's saved value is used.
    assert payload["inputs"] == {"p-env": "prod", "p-tag": "v42"}
    assert "*****" not in json.dumps(payload)
    assert calls["followed"][0]["run_id"] == NEW_RUN_ID
    assert calls["followed"][0].get("cancel_on_interrupt", True) is True


def test_rerun_param_overrides_an_earlier_value(api):
    state, calls = api
    state["run"] = _run(inputs={"p-env": "prod", "p-tag": "v42"})
    state["workflow"] = _workflow(password_default="saved")
    result = runner.invoke(cli.app, ["rerun", "3f2a", "-p", "TAG=v43"])
    assert result.exit_code == 0, result.output
    assert calls["triggered"][0][1]["inputs"] == {"p-env": "prod", "p-tag": "v43"}


def test_rerun_refuses_without_a_terminal_when_a_secret_must_be_retyped(api):
    _state, calls = api                     # the password has no saved value
    result = runner.invoke(cli.app, ["rerun", "3f2a"])
    assert result.exit_code == 1
    assert "hidden prompt" in result.output
    assert calls["triggered"] == []


def test_rerun_of_a_deleted_workflow_says_so(api, monkeypatch):
    _state, calls = api

    def gone(client, wid):
        raise ApiError("Not found.", status=404)

    monkeypatch.setattr(resources, "get_workflow_detail", gone)
    result = runner.invoke(cli.app, ["rerun", "3f2a"])
    assert result.exit_code == 1
    assert "no longer exists" in result.output
    assert calls["triggered"] == []


# --- watch -----------------------------------------------------------------------

def test_watch_a_finished_run_just_shows_it(api):
    state, calls = api
    state["run"] = _run(status="success")
    result = runner.invoke(cli.app, ["watch", "3f2a"])
    assert result.exit_code == 0, result.output
    assert "nothing to follow" in result.output
    assert calls["followed"] == []


def test_watch_attaches_without_cancelling_and_starts_from_the_nodes_done(api):
    state, calls = api
    state["run"] = _run(status="running", inputs={"p-env": "prod", "p-pw": "*****"})
    state["nodes"] = [
        {"node_id": "t1", "status": "success", "started_at": "2026-11-25T10:00:01+00:00",
         "finished_at": "2026-11-25T10:00:01+00:00"},
        {"node_id": "a1", "status": "running", "started_at": "2026-11-25T10:00:02+00:00"},
    ]
    result = runner.invoke(cli.app, ["watch", "3f2a"])
    assert result.exit_code == 0, result.output
    followed, = calls["followed"]
    assert followed["cancel_on_interrupt"] is False
    assert followed["status"] == "running"
    assert followed["view"].state["a1"]["status"] == "running"
    assert followed["view"].state["t1"]["status"] == "success"
    assert calls["cancelled"] == []


def test_ctrl_c_while_watching_detaches_instead_of_cancelling(monkeypatch):
    cancelled = []
    monkeypatch.setattr(resources, "cancel_workflow_run", lambda client, rid: cancelled.append(rid))

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_pump_workflow_stream", interrupted)
    view = cli.runview.WorkflowRunView.from_workflow(_workflow())
    code = cli._follow_workflow_run(object(), RUN_ID, view, None, status="running",
                                    cancel_on_interrupt=False)
    assert code == 130
    assert cancelled == []


# --- cancel ----------------------------------------------------------------------

def test_cancel_without_a_terminal_needs_yes(api):
    state, calls = api
    state["run"] = _run(status="running")
    result = runner.invoke(cli.app, ["cancel", "3f2a"])
    assert result.exit_code == 1
    assert "--yes" in result.output
    assert calls["cancelled"] == []


def test_cancel_with_yes(api):
    state, calls = api
    state["run"] = _run(status="queued")
    result = runner.invoke(cli.app, ["cancel", "3f2a", "--yes"])
    assert result.exit_code == 0, result.output
    assert calls["cancelled"] == [RUN_ID]


def test_cancel_a_finished_run_does_nothing(api):
    state, calls = api
    state["run"] = _run(status="success")
    result = runner.invoke(cli.app, ["cancel", "3f2a", "--yes"])
    assert result.exit_code == 1
    assert "already finished" in result.output
    assert calls["cancelled"] == []


# --- where runs are saved ----------------------------------------------------------

def test_copy_run_saves_where_a_followed_run_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(resources, "get_run_nodes", lambda client, rid: [])
    paths = copier.copy_run(object(), tmp_path, _run(status="success"))
    folder = tmp_path / "logs" / "workflow" / RUN_ID
    assert {p.parent for p in paths} == {folder}
    assert folder == copier.run_folder(tmp_path, RUN_ID)
    assert json.loads((folder / "run.json").read_text(encoding="utf-8"))["id"] == RUN_ID


# --- move key ---------------------------------------------------------------------------

@pytest.fixture
def vaults(monkeypatch):
    key = {"id": "c-1", "name": "deploy-key", "vault": "v-old", "credential_type": "ssh_key"}
    state = {
        "vaults": [
            {"id": "v-old", "name": "Old", "credentials": [key], "servers": []},
            {"id": "v-new", "name": "New", "credentials": [], "servers": []},
        ],
        "workflows": {"wf-1": {"id": "wf-1", "name": "Deploy", "nodes": [
            {"id": "a1", "type": "action", "data": {"vaultDetails": {"vaultId": "v-old", "credentialId": "c-1"}}}]},
            "wf-2": {"id": "wf-2", "name": "Other", "nodes": []}},
        "moved": [],
    }
    monkeypatch.setattr(cli, "_client_or_exit", lambda: object())
    monkeypatch.setattr(resources, "list_vaults_full", lambda client: state["vaults"])
    monkeypatch.setattr(resources, "resolve_credential", lambda client, ref: key)
    monkeypatch.setattr(resources, "list_workflows_full",
                        lambda client: [{"id": w} for w in state["workflows"]])
    monkeypatch.setattr(resources, "get_workflow_detail", lambda client, wid: state["workflows"][wid])
    monkeypatch.setattr(resources, "move_credential",
                        lambda client, cid, vid: state["moved"].append((cid, vid)) or {})
    return state


def test_move_key_lists_the_workflows_that_use_it(vaults):
    result = runner.invoke(cli.app, ["move", "key", "deploy-key", "--to", "New", "--yes"])
    assert result.exit_code == 0, result.output
    assert vaults["moved"] == [("c-1", "v-new")]
    assert "1 workflow(s) use it" in result.output and "Deploy" in result.output
    assert "Other" not in result.output


def test_move_key_is_refused_while_a_server_uses_it(vaults):
    vaults["vaults"][0]["servers"] = [{"id": "s-1", "name": "web-1", "credential": "c-1"}]
    result = runner.invoke(cli.app, ["move", "key", "deploy-key", "--to", "New", "--yes"])
    assert result.exit_code == 1
    assert "web-1" in result.output and "--unlink-key" in result.output
    assert vaults["moved"] == []


def test_move_key_refuses_a_name_clash_and_needs_yes_without_a_terminal(vaults):
    result = runner.invoke(cli.app, ["move", "key", "deploy-key", "--to", "New"])
    assert result.exit_code == 1 and "--yes" in result.output
    vaults["vaults"][1]["credentials"] = [{"id": "c-2", "name": "Deploy-Key"}]
    result = runner.invoke(cli.app, ["move", "key", "deploy-key", "--to", "New", "--yes"])
    assert result.exit_code == 1 and "already has a key" in result.output
    assert vaults["moved"] == []


# --- Sage ---------------------------------------------------------------------------

@pytest.mark.parametrize("words, text", [
    (["rerun", "3f2a"], "Run the workflow of run 3f2a again, with that run's inputs"),
    (["watch", "3f2a"], "Follow run 3f2a live until it ends"),
    (["cancel", "3f2a"], "Cancel run 3f2a"),
])
def test_sage_run_commands_are_changes_with_plain_descriptions(words, text):
    check = sage_tools.check(words)
    assert check.error is None and not check.read_only
    assert sage_tools.describe(check) == text


def test_sage_cancel_card_replaces_the_prompt_and_warns():
    check = sage_tools.check(["cancel", "3f2a"])
    assert check.takes_yes
    assert sage_tools.approved_words(["cancel", "3f2a"], check)[-1] == "--yes"
    assert "won't run" in sage_tools.warning(check)


def test_run_commands_are_in_sages_index():
    index = sage_tools.command_index()
    for name in ("rerun <ref>", "watch <ref>", "cancel <ref>"):
        assert name in index
