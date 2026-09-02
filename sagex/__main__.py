"""sagex entry point.

    python -m sagex               -> launch the terminal app (TUI)
    python -m sagex auth login    -> store an API key (paste when prompted)
    python -m sagex auth status   -> check whether you're authenticated
    python -m sagex auth logout   -> remove the stored key

Typer parses the command line: with no subcommand, the callback launches the TUI;
otherwise the matching `auth` command runs as a plain CLI action.
"""

import typer

from sagex import config
from sagex.api import ApiError, build_client
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

# Lightweight authenticated endpoint used to verify a key.
_VERIFY_PATH = "/api/users/profile/"


@app.callback(invoke_without_command=True)
def _default(ctx: typer.Context) -> None:
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


def _check(raise_on_fail: bool) -> None:
    """Verify the stored key against the backend and print the result."""
    key = store.get_key()
    if not key:
        typer.echo("Not logged in. Run:  python -m sagex auth login")
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
