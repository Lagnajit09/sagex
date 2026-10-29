"""Release smoke tests: the command surface loads, and user-facing behaviour that
must hold in production (generic network errors, no secret leaks, key handling)."""

import httpx
import pytest
from typer.testing import CliRunner

from sagex import __version__, config
from sagex.__main__ import app
from sagex.api import ApiClient, ApiError, resources, store

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
    result = runner.invoke(app, ["auth", "login"], input="not-a-key\n")
    assert result.exit_code == 1
    assert "sgx_" in result.output


def test_login_does_not_store_a_rejected_key(monkeypatch):
    saved = []
    monkeypatch.setattr(store, "set_key", saved.append)

    def reject(self, path, **kwargs):
        raise ApiError("nope", status=401)
    monkeypatch.setattr(ApiClient, "get", reject)

    result = runner.invoke(app, ["auth", "login"], input="sgx_badkey\n")
    assert result.exit_code == 1
    assert saved == []


def test_status_when_logged_out():
    result = runner.invoke(app, ["auth", "status"])
    assert result.exit_code == 1
    assert "sagex auth login" in result.output
