"""sagex entry point.

    sagex               -> launch the terminal app (TUI)
    sagex auth login    -> store an API key (paste when prompted)
    sagex auth status   -> check whether you're authenticated
    sagex auth logout   -> remove the stored key

Typer parses the command line: with no subcommand, the callback launches the TUI;
otherwise the matching `auth` command runs as a plain CLI action.
"""

import sys

import typer

# Windows terminals often default to a legacy code page (cp1252) that can't encode
# characters like ✓/✗, which would crash typer.echo / Rich mid-output. Force UTF-8
# so output is safe regardless of the console's codepage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from sagex import __version__, config, render
from sagex.api import ApiError, build_client, resources
from sagex.api import store
from sagex.app import SagexApp

app = typer.Typer(
    help="sagex — a terminal app for Autosage.",
    add_completion=False,
    no_args_is_help=False,          # no args -> run the callback (launch the TUI)
)
auth_app = typer.Typer(help="Manage authentication (API key).")
app.add_typer(auth_app, name="auth")

workspace_app = typer.Typer(help="Manage the local workspace folder.")
app.add_typer(workspace_app, name="workspace")

show_app = typer.Typer(help="Show a single resource's details in the terminal.")
app.add_typer(show_app, name="show")

# Lightweight authenticated endpoint used to verify a key.
_VERIFY_PATH = "/api/users/profile/"


def _version_callback(value: bool) -> None:
    """Print the version and exit (an 'eager' option, handled before anything else)."""
    if value:
        typer.echo(f"sagex {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _default(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Launch the terminal app when no subcommand is given."""
    if ctx.invoked_subcommand is None:
        SagexApp().run()


@auth_app.command("login")
def auth_login() -> None:
    """Store an API key (paste it when prompted)."""
    key = typer.prompt("Paste your sagex API key", hide_input=True).strip()
    if not key:
        typer.echo("No key entered.")
        raise typer.Exit(code=1)
    store.set_key(key)
    typer.echo("Key saved to your OS keychain.")
    _check(raise_on_fail=False)     # verify right away, but don't hard-fail login


@auth_app.command("status")
def auth_status() -> None:
    """Show whether you're authenticated."""
    _check(raise_on_fail=True)


@auth_app.command("logout")
def auth_logout() -> None:
    """Remove the stored API key."""
    store.delete_key()
    typer.echo("Logged out — API key removed.")


@workspace_app.command("set")
def workspace_set(path: str) -> None:
    """Set the local workspace folder (relative or absolute path)."""
    try:
        resolved = config.resolve_workspace(path)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    settings = config.load()
    settings["workspace"] = resolved
    config.save(settings)
    typer.echo(f"Workspace set to: {resolved}")


@workspace_app.command("show")
def workspace_show() -> None:
    """Show the current workspace folder."""
    ws = config.load().get("workspace")
    typer.echo(ws or "(not set — defaults to the current directory)")


def _client_or_exit() -> "build_client":
    """Return an API client, or exit early with a friendly message if no key."""
    if not store.get_key():
        typer.echo("Not logged in. Run:  sagex auth login")
        raise typer.Exit(code=1)
    return build_client()


def _resolve_or_exit(resolve, client, ref: str, kind: str):
    """Run a resolver, turning its expected errors into clean CLI exits."""
    try:
        return resolve(client, ref)
    except resources.AmbiguousResource as exc:
        render.ambiguous(exc)
        raise typer.Exit(code=1)
    except resources.ResourceNotFound as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1)
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)


@show_app.command("workflow")
def show_workflow_cmd(
    ref: str = typer.Argument(..., help="Workflow name or id."),
    json_out: bool = typer.Option(False, "--json", help="Print the raw record as JSON."),
) -> None:
    """Show a workflow's details (including its nodes and edges)."""
    client = _client_or_exit()
    wf = _resolve_or_exit(resources.resolve_workflow, client, ref, "workflow")
    render.print_json(wf) if json_out else render.workflow(wf)


@show_app.command("script")
def show_script_cmd(
    ref: str = typer.Argument(..., help="Script name or id."),
    json_out: bool = typer.Option(False, "--json", help="Print the raw record as JSON."),
    no_content: bool = typer.Option(False, "--no-content", help="Skip the code body."),
) -> None:
    """Show a script's metadata and (by default) its code, syntax-highlighted."""
    client = _client_or_exit()
    meta = _resolve_or_exit(resources.resolve_script, client, ref, "script")
    if json_out:
        render.print_json(meta)
        return
    content = None
    if not no_content:
        try:
            content = resources.get_script_content(client, meta["id"])
        except ApiError as exc:
            typer.echo(f"(couldn't load code body: {exc.message})")   # still show metadata
    render.script(meta, content)


@show_app.command("run")
def show_run_cmd(
    ref: str = typer.Argument(..., help="Run id (or an id prefix)."),
    json_out: bool = typer.Option(False, "--json", help="Print the raw record as JSON."),
    logs: bool = typer.Option(False, "--logs", help="Also download and print each node's stdout/stderr."),
) -> None:
    """Show a run's status and per-node results (and optionally its logs)."""
    client = _client_or_exit()
    run = _resolve_or_exit(resources.resolve_run, client, ref, "run")
    if json_out:
        render.print_json(run)
        return

    nodes: list = []
    try:
        nodes = resources.get_run_nodes(client, run["id"])
    except ApiError as exc:
        typer.echo(f"(couldn't load node results: {exc.message})")

    render.run(run, nodes)

    # Logs live in GCS behind signed URLs and are swept after ~90 days; the server
    # tells us via `logs_expired` on any node (it's keyed off the run's age).
    if any(n.get("logs_expired") for n in nodes):
        render.note("Logs are not available for this run — we keep logs for 90 days.")
    elif logs:
        entries = []
        for n in nodes:
            label = n.get("node_label") or n.get("node_id") or "node"
            for stream in ("stdout", "stderr"):
                url = n.get(f"{stream}_signed_url")
                if url:
                    try:
                        text = resources.fetch_log_text(url)
                    except ApiError as exc:
                        text = f"({exc.message})"
                    entries.append((label, stream, text))
        render.run_logs(entries)
    elif any(n.get("stdout_signed_url") or n.get("stderr_signed_url") for n in nodes):
        render.note("Logs available — pass --logs to print them.")


@show_app.command("trigger")
def show_trigger_cmd(
    ref: str = typer.Argument(..., help="Trigger id or workflow name."),
    json_out: bool = typer.Option(False, "--json", help="Print the raw record as JSON."),
) -> None:
    """Show a trigger's detail (schedule or HTTP). Never prints the full secret."""
    client = _client_or_exit()
    kind, detail = _resolve_or_exit(resources.resolve_trigger, client, ref, "trigger")
    render.print_json(detail) if json_out else render.trigger(kind, detail)


@show_app.command("key")
def show_key_cmd(
    ref: str = typer.Argument(..., help="Credential (key) id or name."),
    reveal: bool = typer.Option(False, "--reveal", help="Fetch and display the secret values (asks first)."),
) -> None:
    """Show a vault credential. Secrets stay hidden unless you pass --reveal."""
    client = _client_or_exit()
    cred = _resolve_or_exit(resources.resolve_credential, client, ref, "key")

    secrets = None
    if reveal:
        name = cred.get("name") or cred.get("id")
        if not typer.confirm(f"Reveal plaintext secrets for '{name}'? They will be printed here."):
            typer.echo("Aborted — secrets not fetched.")
            raise typer.Exit(code=1)
        try:
            secrets = resources.reveal_credential(client, cred["id"])
        except ApiError as exc:
            typer.echo(f"✗ {exc.message}")
            raise typer.Exit(code=1)
    render.credential(cred, secrets)


@show_app.command("server")
def show_server_cmd(
    ref: str = typer.Argument(..., help="Server id or name."),
    json_out: bool = typer.Option(False, "--json", help="Print the raw record as JSON."),
) -> None:
    """Show a vault server (host, connection method, linked credential, vault)."""
    client = _client_or_exit()
    server = _resolve_or_exit(resources.resolve_server, client, ref, "server")
    render.print_json(server) if json_out else render.server(server)


@show_app.command("vault")
def show_vault_cmd(
    ref: str = typer.Argument(..., help="Vault id or name."),
    json_out: bool = typer.Option(False, "--json", help="Print the raw record as JSON."),
) -> None:
    """Show a vault and the credentials and servers it contains."""
    client = _client_or_exit()
    vault = _resolve_or_exit(resources.resolve_vault, client, ref, "vault")
    render.print_json(vault) if json_out else render.vault(vault)


def _check(raise_on_fail: bool) -> None:
    """Verify the stored key against the backend and print the result."""
    key = store.get_key()
    if not key:
        typer.echo("Not logged in. Run:  sagex auth login")
        if raise_on_fail:
            raise typer.Exit(code=1)
        return

    client = build_client()
    try:
        client.get(_VERIFY_PATH)
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        if raise_on_fail:
            raise typer.Exit(code=1)
        return

    typer.echo(f"✓ Authenticated to {client.base_url}")


if __name__ == "__main__":
    app()
