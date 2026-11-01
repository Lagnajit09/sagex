"""Release smoke tests: the command surface loads, and user-facing behaviour that
must hold in production (generic network errors, no secret leaks, key handling)."""

import httpx
import pytest
from typer.testing import CliRunner

from sagex import __main__ as cli
from sagex import __version__, config, device
from sagex.__main__ import app
from sagex.api import ApiClient, ApiError, resources, store
from sagex.api import auth as cli_auth
from sagex.api import client as api_client

runner = CliRunner()

GROUPS = ["auth", "workspace", "show", "render", "list", "copy", "push", "new",
          "create", "update", "delete", "trigger", "rename", "run"]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """No real config file, keychain, env overrides, or debug output."""
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    for var in ("SAGEX_API_URL", "SAGEX_API_KEY", "SAGEX_DEBUG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(store.keyring, "get_password", lambda *a: None)
    monkeypatch.setattr(store, "keychain_available", lambda: True)


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"sagex {__version__}"


@pytest.mark.parametrize("group", GROUPS)
def test_every_group_has_help(group):
    result = runner.invoke(app, [group, "--help"])
    assert result.exit_code == 0, result.output


def test_defaults_to_the_hosted_api():
    assert config.api_url() == config.DEFAULT_API_URL
    assert config.DEFAULT_API_URL.startswith("https://")


def test_env_overrides_api_url_without_persisting(monkeypatch):
    monkeypatch.setenv("SAGEX_API_URL", "http://localhost:8080")
    assert config.api_url() == "http://localhost:8080"
    config.save(config.load())                       # e.g. the TUI saving the shell choice
    monkeypatch.delenv("SAGEX_API_URL")
    assert config.api_url() == config.DEFAULT_API_URL


def test_unreachable_server_message_is_generic():
    client = ApiClient("http://127.0.0.1:9", "sgx_test")
    with pytest.raises(ApiError) as info:
        client.get("/api/users/profile/")
    message = info.value.message
    assert "Unable to reach the Autosage server" in message
    assert "127.0.0.1" not in message
    assert "running" not in message.lower()


def test_debug_mode_adds_connection_details(monkeypatch):
    monkeypatch.setenv("SAGEX_DEBUG", "1")
    with pytest.raises(ApiError) as info:
        ApiClient("http://127.0.0.1:9", "sgx_test").get("/x/")
    assert "127.0.0.1:9" in info.value.message


def test_gateway_errors_read_as_temporarily_unavailable():
    resp = httpx.Response(502, text="<html>Bad Gateway</html>")
    with pytest.raises(ApiError) as info:
        ApiClient("https://example.test", "sgx_test")._unwrap(resp)
    assert "temporarily unavailable" in info.value.message
    assert info.value.status == 502


def test_log_download_errors_never_include_the_signed_url(monkeypatch):
    url = "https://storage.googleapis.com/bucket/log?X-Goog-Signature=secret"

    monkeypatch.setattr(resources.httpx, "get",
                        lambda *a, **k: httpx.Response(403, request=httpx.Request("GET", url)))
    with pytest.raises(ApiError) as info:
        resources.fetch_log_text(url)
    assert "Signature" not in info.value.message and "HTTP 403" in info.value.message

    def refuse(*a, **k):
        raise httpx.ConnectError(f"failed to connect to {url}")
    monkeypatch.setattr(resources.httpx, "get", refuse)
    with pytest.raises(ApiError) as info:
        resources.fetch_log_text(url)
    assert "Signature" not in info.value.message


def test_env_key_takes_precedence(monkeypatch):
    monkeypatch.setattr(store.keyring, "get_password", lambda *a: "sgx_from_keychain")
    monkeypatch.setenv("SAGEX_API_KEY", "sgx_from_env")
    assert store.get_key() == "sgx_from_env"


def test_login_rejects_a_non_sagex_key():
    result = runner.invoke(app, ["auth", "login", "--with-token"], input="not-a-key\n")
    assert result.exit_code == 1
    assert "sgx_" in result.output


def test_login_does_not_store_a_rejected_key(monkeypatch):
    saved = []
    monkeypatch.setattr(store, "set_key", saved.append)

    def reject(self, path, **kwargs):
        raise ApiError("nope", status=401)
    monkeypatch.setattr(ApiClient, "get", reject)

    result = runner.invoke(app, ["auth", "login", "--with-token"], input="sgx_badkey\n")
    assert result.exit_code == 1
    assert saved == []


def test_status_when_logged_out():
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 1
    assert "sagex auth login" in result.output


# --- browser sign-in (device flow), sessions and activity headers -------------

START = {
    "device_code": "dc-secret", "user_code": "WDJB-MJHT",
    "verification_uri": "https://app.test/cli-auth",
    "verification_uri_complete": "https://app.test/cli-auth?code=WDJB-MJHT",
    "expires_in": 600, "interval": 5,
}
APPROVED = {
    "status": "approved", "api_key": "sgx_new",
    "session": {"name": "laptop", "kind": "device"},
    "user": {"email": "dev@example.com"},
}


@pytest.fixture
def browser_login(monkeypatch):
    """A fake server for the device flow: pending once, then approved."""
    calls, saved = [], []
    replies = iter([{"status": "pending"}, APPROVED])

    def post(self, path, json=None, headers=None):
        calls.append((path, self.api_key, json))
        return START if path == cli_auth.START_PATH else next(replies)

    monkeypatch.setattr(ApiClient, "post", post)
    monkeypatch.setattr(cli_auth.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "_open_browser", lambda url: False)
    monkeypatch.setattr(store, "set_key", saved.append)
    return calls, saved


def test_browser_login_stores_the_approved_key(browser_login):
    calls, saved = browser_login
    result = runner.invoke(app, ["auth", "login"])
    assert result.exit_code == 0, result.output
    assert "WDJB-MJHT" in result.output
    assert "https://app.test/cli-auth?code=WDJB-MJHT" in result.output
    assert "Logged in to Autosage as dev@example.com on laptop" in result.output
    assert saved == ["sgx_new"]
    # The flow never sends a key, and the device code is never printed.
    assert all(api_key is None for _, api_key, _ in calls)
    assert "dc-secret" not in result.output
    start_body = calls[0][2]
    assert start_body["cli_version"] == __version__
    assert start_body["os"] == device.os_name()


def test_browser_login_reports_a_denied_request(monkeypatch):
    def post(self, path, json=None, headers=None):
        if path == cli_auth.START_PATH:
            return START
        raise ApiError("The sign-in was denied in the browser.", status=403)

    saved = []
    monkeypatch.setattr(ApiClient, "post", post)
    monkeypatch.setattr(cli_auth.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "_open_browser", lambda url: False)
    monkeypatch.setattr(store, "set_key", saved.append)
    result = runner.invoke(app, ["auth", "login"])
    assert result.exit_code == 1
    assert "denied" in result.output
    assert saved == []


def test_login_ends_the_session_it_replaces(browser_login, monkeypatch):
    monkeypatch.setattr(store, "keychain_key", lambda: "sgx_old")
    ended = []
    monkeypatch.setattr(ApiClient, "get", lambda self, path, **k: {"session": {"kind": "device"}})
    monkeypatch.setattr(ApiClient, "delete", lambda self, path: ended.append(self.api_key))
    result = runner.invoke(app, ["auth", "login"])
    assert result.exit_code == 0, result.output
    assert ended == ["sgx_old"]


def test_login_without_a_keychain_points_to_tokens(monkeypatch):
    monkeypatch.setattr(store, "keychain_available", lambda: False)
    result = runner.invoke(app, ["auth", "login"])
    assert result.exit_code == 1
    assert store.ENV_VAR in result.output
    assert "/profile#sagecode" in result.output


def test_logout_signs_out_a_device_session(monkeypatch):
    monkeypatch.setattr(store, "keychain_key", lambda: "sgx_device")
    deleted, removed = [], []
    monkeypatch.setattr(ApiClient, "get", lambda self, path, **k: {"session": {"kind": "device"}})
    monkeypatch.setattr(ApiClient, "delete", lambda self, path: deleted.append(path))
    monkeypatch.setattr(store, "delete_key", lambda: removed.append(True))
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0
    assert deleted == [cli_auth.CURRENT_PATH]
    assert removed == [True]
    assert "signed out" in result.output


def test_logout_leaves_access_tokens_working(monkeypatch):
    monkeypatch.setattr(store, "keychain_key", lambda: "sgx_ci_token")
    deleted = []
    monkeypatch.setattr(ApiClient, "get", lambda self, path, **k: {"session": {"kind": "token"}})
    monkeypatch.setattr(ApiClient, "delete", lambda self, path: deleted.append(path))
    monkeypatch.setattr(store, "delete_key", lambda: None)
    result = runner.invoke(app, ["auth", "logout"])
    assert result.exit_code == 0
    assert deleted == []
    assert "still works" in result.output


def test_status_shows_the_account_and_session(monkeypatch):
    monkeypatch.setenv("SAGEX_API_KEY", "sgx_abcd")
    monkeypatch.setattr(ApiClient, "get", lambda self, path, **k: {
        "user": {"email": "dev@example.com"},
        "session": {"kind": "token", "name": "CI", "last4": "abcd",
                    "commands_run": 3, "runs_started": 1},
    })
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 0, result.output
    assert "as dev@example.com" in result.output
    assert "Access token: CI" in result.output
    assert "3 commands · 1 run started" in result.output


def test_requests_identify_the_cli_and_command(monkeypatch):
    monkeypatch.setattr(api_client, "_command", "run workflow")
    headers = ApiClient("https://example.test", "sgx_k")._headers()
    assert headers["User-Agent"] == f"sagex/{__version__} ({device.os_name()})"
    assert headers["X-Sagex-Command"] == "run workflow"
    assert len(headers["X-Sagex-Invocation"]) == 32
    assert "(" not in device.os_name()


def test_command_label_is_the_subcommand_path(monkeypatch):
    import typer

    ctx = type("Ctx", (), {"command": typer.main.get_command(app)})()
    monkeypatch.setattr(cli.sys, "argv", ["sagex", "run", "workflow", "Deploy", "--watch"])
    assert cli._command_label(ctx) == "run workflow"
    monkeypatch.setattr(cli.sys, "argv", ["sagex", "list", "--json", "workflow"])
    assert cli._command_label(ctx) == "list workflow"


def test_dead_session_message_comes_from_the_server():
    detail = ("This SageCode session expired after 90 days without use. "
              "Sign in again: sagex auth login")
    body = {"success": False, "errors": {"detail": detail}}
    with pytest.raises(ApiError) as info:
        ApiClient("https://example.test", "sgx_k")._unwrap(httpx.Response(401, json=body))
    assert info.value.message == detail


def test_unknown_key_gets_the_generic_message():
    body = {"success": False, "errors": {"detail": "Invalid API key."}}
    with pytest.raises(ApiError) as info:
        ApiClient("https://example.test", "sgx_k")._unwrap(httpx.Response(401, json=body))
    assert "sagex auth login" in info.value.message
