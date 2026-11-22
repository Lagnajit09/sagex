"""Personal models (BYO): `sagex list|show|create|update|rename|delete|use model`,
how Sage may use them, and /model's argument parsing (sagex/models.py)."""

import pytest
from typer.testing import CliRunner

from sagex import __main__ as cli
from sagex import config, models, sage_tools
from sagex.__main__ import app
from sagex.api import ApiClient, store

runner = CliRunner()
SECRET = "sk-ant-test-0123456789"


class FakeDjango:
    """The llm-configs and settings endpoints, in memory."""

    def __init__(self):
        self.configs = [{"id": "m1", "name": "My Claude", "provider": "anthropic",
                         "model_name": "claude-sonnet-4-5", "base_url": "", "api_version": "",
                         "system_instruction": "", "modified_at": "2026-11-20T10:00:00Z"}]
        self.selected = None
        self.writes = []                          # (method, path, body)

    def get(self, client, path, params=None, headers=None):
        if path == "/api/autobot/llm-configs/":
            return [dict(c) for c in self.configs]
        if path == "/api/autobot/settings/":
            return {"default_llm_config": self.selected}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, client, path, json=None, headers=None):
        self.writes.append(("POST", path, dict(json)))
        created = {k: v for k, v in json.items() if k != "api_key"} | {"id": f"m{len(self.configs) + 1}"}
        self.configs.append(created)
        return created

    def patch(self, client, path, json=None):
        self.writes.append(("PATCH", path, dict(json)))
        if path == "/api/autobot/settings/":
            self.selected = json["default_llm_config"]
            return {"default_llm_config": self.selected}
        model_id = path.rstrip("/").rsplit("/", 1)[-1]
        target = next(c for c in self.configs if c["id"] == model_id)
        target.update({k: v for k, v in json.items() if k != "api_key"})
        return dict(target)

    def delete(self, client, path):
        self.writes.append(("DELETE", path, None))
        model_id = path.rstrip("/").rsplit("/", 1)[-1]
        self.configs = [c for c in self.configs if c["id"] != model_id]


@pytest.fixture
def django(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(store, "get_key", lambda: "sgx_test")
    fake = FakeDjango()
    for name in ("get", "post", "patch", "delete"):
        handler = getattr(fake, name)
        monkeypatch.setattr(ApiClient, name, lambda self, *a, _h=handler, **k: _h(self, *a, **k))
    return fake


def _key_file(tmp_path, text=SECRET + "\n"):
    path = tmp_path / "key.txt"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_create_reads_the_key_from_a_file_and_never_prints_it(django, tmp_path):
    result = runner.invoke(app, ["create", "model", "Groq fast", "--provider", "groq",
                                 "--model", "groq/llama-3.3-70b-versatile", "--key-file", _key_file(tmp_path)])
    assert result.exit_code == 0, result.output
    method, path, body = django.writes[0]
    assert (method, path) == ("POST", "/api/autobot/llm-configs/")
    assert body["api_key"] == SECRET and body["provider"] == "groq"
    assert SECRET not in result.output
    assert "Groq fast · groq/llama-3.3-70b-versatile" in result.output
    assert "sagex use model" in result.output and django.selected is None


def test_create_without_a_terminal_or_key_file_asks_for_neither(django):
    # CliRunner isn't a terminal — like the TUI's `!` commands.
    result = runner.invoke(app, ["create", "model", "X", "--provider", "openai", "--model", "gpt-4o-mini"])
    assert result.exit_code == 1 and "hidden prompt" in result.output and "--key-file" in result.output
    assert django.writes == []


def test_create_checks_the_name_before_asking_for_the_key(django, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_model_api_key", lambda f: pytest.fail("asked for the key"))
    result = runner.invoke(app, ["create", "model", "my claude", "--provider", "anthropic",
                                 "--model", "claude-opus-4"])
    assert result.exit_code == 1 and "already have a model named 'My Claude'" in result.output


def test_create_with_use_selects_it(django, tmp_path):
    result = runner.invoke(app, ["create", "model", "Mine", "-p", "openai", "-m", "gpt-4o-mini",
                                 "--key-file", _key_file(tmp_path), "--use"])
    assert result.exit_code == 0, result.output
    assert django.selected == "m2" and "Sage now answers with Mine" in result.output
    assert "Autobot on the web" in result.output


@pytest.mark.parametrize("args, message", [
    (["-p", "azure_openai", "-m", "my-deployment"], "--api-version"),
    (["-p", "bard", "-m", "x"], "--provider must be one of"),
    (["-p", "custom", "-m", "x", "--base-url", "ftp://proxy"], "--base-url must start with"),
])
def test_create_refuses_bad_settings_before_anything_else(django, args, message):
    result = runner.invoke(app, ["create", "model", "X", *args])
    assert result.exit_code == 1 and message in result.output and django.writes == []


def test_create_refuses_a_multi_line_key_file(django, tmp_path):
    result = runner.invoke(app, ["create", "model", "X", "-p", "openai", "-m", "gpt-4o-mini",
                                 "--key-file", _key_file(tmp_path, "one\ntwo\n")])
    assert result.exit_code == 1 and "only the API key" in result.output and django.writes == []


def test_list_marks_the_model_in_use(django):
    result = runner.invoke(app, ["list", "model"])
    assert result.exit_code == 0
    assert "Autosage free models" in result.output and "My Claude" in result.output
    assert "anthropic/claude-sonnet-4-5" in result.output
    django.selected = "m1"
    import json
    rows = json.loads(runner.invoke(app, ["list", "model", "--json"]).output)
    assert rows[0]["in_use"] is True and "api_key" not in rows[0]


def test_show_never_has_a_key_to_show(django):
    result = runner.invoke(app, ["show", "model", "My Claude"])
    assert result.exit_code == 0 and "never shown" in result.output


def test_use_by_name_and_back_to_free(django):
    result = runner.invoke(app, ["use", "model", "my claude"])
    assert result.exit_code == 0 and django.selected == "m1"
    assert "Sage now answers with My Claude · anthropic/claude-sonnet-4-5" in result.output
    result = runner.invoke(app, ["use", "model", "--free"])
    assert result.exit_code == 0 and django.selected is None and "Autosage free models" in result.output
    assert runner.invoke(app, ["use", "model"]).exit_code == 1
    assert "No model matching" in runner.invoke(app, ["use", "model", "nope"]).output


def test_update_keeps_the_stored_key_unless_asked(django, tmp_path):
    result = runner.invoke(app, ["update", "model", "My Claude", "--model", "claude-opus-4"])
    assert result.exit_code == 0, result.output
    assert "api_key" not in django.writes[-1][2] and django.configs[0]["model_name"] == "claude-opus-4"
    result = runner.invoke(app, ["update", "model", "My Claude", "--key-file", _key_file(tmp_path)])
    assert result.exit_code == 0 and django.writes[-1][2] == {"api_key": SECRET}
    assert "new API key saved" in result.output and SECRET not in result.output
    assert "Nothing to update" in runner.invoke(app, ["update", "model", "My Claude"]).output


def test_rename_and_delete(django):
    assert runner.invoke(app, ["rename", "model", "My Claude", "Claude"]).exit_code == 0
    assert django.configs[0]["name"] == "Claude"
    django.selected = "m1"
    result = runner.invoke(app, ["delete", "model", "Claude"], input="y\n")
    assert result.exit_code == 0 and "switch to Autosage free models" in result.output
    assert django.configs == []


# --- what Sage may do with them --------------------------------------------------

def test_sage_never_gets_the_commands_that_take_a_key():
    index = sage_tools.command_index()
    assert "create model" not in index and "update model" not in index
    assert sage_tools.check(["create", "model", "x", "-p", "openai", "-m", "y"]).error
    assert sage_tools.check(["list", "model"]).read_only
    assert sage_tools.check(["show", "model", "My Claude"]).read_only


def test_sage_switching_models_asks_with_a_warning_from_sagex():
    check = sage_tools.check(["use", "model", "My Claude"])
    assert check.error is None and not check.read_only
    assert "Autobot on the web" in sage_tools.warning(check)
    assert sage_tools.check(["delete", "model", "My Claude"]).always_ask


# --- /model arguments --------------------------------------------------------------

ITEMS = [{"id": "m1", "name": "My Claude"}, {"id": "m2", "name": "Groq"}]


@pytest.mark.parametrize("ref, expected", [
    ("0", None), ("free", None), ("FREE", None), ("1", ITEMS[0]), ("2", ITEMS[1]), ("groq", ITEMS[1]),
])
def test_pick(ref, expected):
    assert models.pick(ITEMS, ref) == expected


def test_pick_explains_a_miss():
    assert "0–2" in models.pick(ITEMS, "3")
    assert "No model named" in models.pick(ITEMS, "llama")


def test_labels():
    assert models.wire_name({"provider": "groq", "model_name": "groq/llama"}) == "groq/llama"
    assert models.wire_name({"provider": "openai", "model_name": "gpt-4o"}) == "openai/gpt-4o"
    assert models.label(None) == models.FREE_LABEL
