"""sagex entry point.

    sagex               -> launch the terminal app (TUI)
    sagex auth login    -> store an API key (paste when prompted)
    sagex auth status   -> check whether you're authenticated
    sagex auth logout   -> remove the stored key

Typer parses the command line: with no subcommand, the callback launches the TUI;
otherwise the matching `auth` command runs as a plain CLI action.
"""

import json
import sys
from pathlib import Path

import typer

# Windows terminals often default to a legacy code page (cp1252) that can't encode
# characters like ✓/✗, which would crash typer.echo / Rich mid-output. Force UTF-8
# so output is safe regardless of the console's codepage.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from sagex import __version__, config, copier, pusher, render
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

render_app = typer.Typer(help="Render a workflow as a flow tree in the terminal.")
app.add_typer(render_app, name="render")

list_app = typer.Typer(help="List resources in a compact table.")
app.add_typer(list_app, name="list")

copy_app = typer.Typer(help="Copy resources to your local workspace.")
app.add_typer(copy_app, name="copy")

push_app = typer.Typer(help="Push local resource files to the server (create/update).")
app.add_typer(push_app, name="push")

new_app = typer.Typer(help="Scaffold new local resource files ready to edit and push.")
app.add_typer(new_app, name="new")

create_app = typer.Typer(help="Create server-side resources (vaults, servers, keys).")
app.add_typer(create_app, name="create")

update_app = typer.Typer(help="Update server-side resources (vaults, servers, keys).")
app.add_typer(update_app, name="update")

# Lightweight authenticated endpoint used to verify a key.
_VERIFY_PATH = "/api/users/profile/"

# ---------------------------------------------------------------------------
# Workflow scaffold template — embedded in files created by `sagex new workflow`.
# _doc is stripped by workflow_payload() so it's never sent to the server.
# ---------------------------------------------------------------------------
_WORKFLOW_TEMPLATE: dict = {
    "_doc": [
        "Workflow file created by `sagex new workflow`.",
        "Edit it, then run: sagex push workflow <this-file>",
        "Get a full copy-paste snippet for any node/edge with: sagex syntax <kind>",
        "",
        "REQUIRED TOP-LEVEL FIELDS",
        "  name        (string)  display name shown in the web app",
        "  description (string)  optional free text",
        "  nodes       (list)    each node needs: id, type, position {x, y}, data",
        "  edges       (list)    each edge needs: id, source, target",
        "  (a new file has no 'id' -- push creates the workflow and writes its id back here)",
        "",
        "NODE TYPES",
        "  'trigger'  -- entry point; every workflow needs exactly one.",
        "               data.type: 'manual' | 'schedule' | 'http'",
        "",
        "  'action' + data.type 'script' -- runs a script on a server.",
        "     data.selectedScript.scriptId    (required) id (string) from `sagex list script`",
        "     data.selectedScript.type        script kind label, e.g. 'Shell Script'",
        "     data.vaultDetails.vaultId       (required) id from `sagex list vault`",
        "     data.vaultDetails.serverId      (required) id from `sagex list server`",
        "     data.vaultDetails.credentialId  (required) id from `sagex list key`",
        "     data.executionMode              'remote' (run on the vault's server)",
        "     data.outputFormat               'json' | 'text'",
        "     data.parameters[]               {id, name, type, value, sourceType, description}",
        "                                     sourceType 'manual' = literal; 'output' = {{node-id.output.key}}",
        "                                     type: string | number | boolean | password",
        "     data.jsonSchema[]               {name, type} -- declares this script's output fields",
        "",
        "  'action' + data.type 'email' -- sends an email.",
        "     data.smtpConfig.{host, port, vaultId, credentialId}  (required; credential = username_password)",
        "     data.smtpConfig.secure          true to use TLS",
        "     data.to (list) and data.subject (string)             (required)",
        "     data.from / data.cc / data.bcc / data.body           (optional)",
        "",
        "  'decision' -- conditional branch; needs exactly 2 outgoing edges.",
        "     data.conditions[]   {id, field, operator, value, fieldSource, valueSource}",
        "                         operator e.g. '==' / '!=';  *Source: 'output' ({{...}}) or 'manual'",
        "     data.trueLabel      list of node ids taken when conditions pass",
        "     data.falseLabel     list of node ids taken otherwise",
        "     Its two edges must carry sourceHandle: 'true' and 'false'.",
        "",
        "EDGES",
        "  {id, source, target} connect nodes; type 'smoothstep' + style are UI hints.",
        "  A decision node's two edges also need sourceHandle 'true' / 'false'.",
        "",
        "CREATE vs UPDATE",
        "  No 'id' field  -> push creates a new workflow and writes the id back here.",
        "  Has 'id' field -> push updates that workflow (asks for confirmation).",
        "  Wrong/stale id -> server returns 404; re-run push with --new to force-create.",
    ],
    "name": "",
    "description": "",
    "nodes": [
        {
            "id": "trigger-1",
            "type": "trigger",
            "position": {"x": 0, "y": 200},
            "data": {"type": "manual", "label": "Manual Trigger", "description": ""},
            "measured": {"width": 160, "height": 160},
        },
        {
            "id": "action-1",
            "type": "action",
            "position": {"x": 250, "y": 200},
            "data": {
                "type": "script",
                "label": "Run Script",
                "description": "",
                "executionMode": "remote",
                "outputFormat": "json",
                "selectedScript": {"type": "Shell Script", "scriptId": "REPLACE_WITH_SCRIPT_ID"},
                "vaultDetails": {
                    "vaultId": "REPLACE_WITH_VAULT_ID",
                    "serverId": "REPLACE_WITH_SERVER_ID",
                    "credentialId": "REPLACE_WITH_CREDENTIAL_ID",
                },
                "parameters": [],
                "jsonSchema": [],
            },
            "measured": {"width": 275, "height": 102},
        },
    ],
    "edges": [
        {
            "id": "xy-edge__trigger-1-action-1",
            "type": "smoothstep",
            "source": "trigger-1",
            "target": "action-1",
            "style": {"stroke": "#9CA3AF", "strokeWidth": 2},
        }
    ],
}


# Snippets mirror the web app's export shape (see a real export for reference).
# ids/values are placeholders; REPLACE_WITH_* markers are the ones you must fill in.
_SYNTAX_SNIPPETS: dict[str, object] = {
    "trigger": {
        "id": "trigger-1",
        "type": "trigger",
        "position": {"x": 0, "y": 200},
        "data": {"type": "manual", "label": "Manual Trigger", "description": ""},
        "measured": {"width": 160, "height": 160},
    },
    "script": {
        "id": "action-1",
        "type": "action",
        "position": {"x": 250, "y": 200},
        "data": {
            "type": "script",
            "label": "Run Script",
            "description": "",
            "executionMode": "remote",
            "outputFormat": "json",
            "selectedScript": {"type": "Shell Script", "scriptId": "REPLACE_WITH_SCRIPT_ID"},
            "vaultDetails": {
                "vaultId": "REPLACE_WITH_VAULT_ID",
                "serverId": "REPLACE_WITH_SERVER_ID",
                "credentialId": "REPLACE_WITH_CREDENTIAL_ID",
            },
            "parameters": [
                {
                    "id": "param-1",
                    "name": "SERVICE_NAME",
                    "type": "string",
                    "value": "nginx",
                    "sourceType": "manual",
                    "description": "",
                }
            ],
            "jsonSchema": [
                {"name": "service_name", "type": "string"},
                {"name": "status", "type": "string"},
            ],
        },
        "measured": {"width": 275, "height": 102},
    },
    "email": {
        "id": "action-1",
        "type": "action",
        "position": {"x": 250, "y": 200},
        "data": {
            "type": "email",
            "label": "Send Email",
            "description": "",
            "from": "sender@example.com",
            "to": ["recipient@example.com"],
            "cc": [],
            "bcc": [],
            "subject": "Subject here",
            "body": "Email body here.",
            "smtpConfig": {
                "host": "smtp.gmail.com",
                "port": 587,
                "secure": False,
                "vaultId": "REPLACE_WITH_VAULT_ID",
                "credentialId": "REPLACE_WITH_CREDENTIAL_ID",
            },
        },
        "measured": {"width": 185, "height": 102},
    },
    "decision": {
        "id": "decision-1",
        "type": "decision",
        "position": {"x": 500, "y": 200},
        "data": {
            "label": "Condition Check",
            "description": "",
            "conditions": [
                {
                    "id": "cond-1",
                    "field": "{{action-1.output.exists}}",
                    "value": "true",
                    "operator": "==",
                    "fieldSource": "output",
                    "valueSource": "manual",
                }
            ],
            "trueLabel": ["action-on-true"],
            "falseLabel": ["action-on-false"],
        },
        "measured": {"width": 160, "height": 160},
    },
    "edge": {
        "id": "xy-edge__source-target",
        "type": "smoothstep",
        "source": "source-node-id",
        "target": "target-node-id",
        "style": {"stroke": "#9CA3AF", "strokeWidth": 2},
    },
    "decision-edge": [
        {
            "id": "decision-1-action-on-true-true",
            "type": "smoothstep",
            "label": "True",
            "source": "decision-1",
            "target": "action-on-true",
            "sourceHandle": "true",
            "style": {"stroke": "#10b981", "strokeWidth": 2},
        },
        {
            "id": "decision-1-action-on-false-false",
            "type": "smoothstep",
            "label": "False",
            "source": "decision-1",
            "target": "action-on-false",
            "sourceHandle": "false",
            "style": {"stroke": "#ef4444", "strokeWidth": 2},
        },
    ],
}

_SYNTAX_NOTES: dict[str, list[str]] = {
    "trigger": [
        "Entry point — exactly one per workflow. Required: id, type, position, data.type, data.label.",
        "data.type: 'manual' | 'schedule' | 'http' (schedule/http need their trigger settings from the web app).",
    ],
    "script": [
        "Action that runs a script on a server.",
        "Required: selectedScript.scriptId, vaultDetails.{vaultId, serverId, credentialId}.",
        "parameters[].sourceType: 'manual' (literal value) or 'output' (value is a {{node-id.output.key}} reference).",
        "parameters[].type: string | number | boolean | password.   outputFormat: 'json' | 'text'.",
        "jsonSchema declares this script's outputs so later nodes can read {{this-id.output.<name>}}.",
    ],
    "email": [
        "Action that sends an email.",
        "Required: smtpConfig.{host, port, vaultId, credentialId}, subject, to[].",
        "credentialId must be a username_password credential. from / cc / bcc / body are optional.",
    ],
    "decision": [
        "Branch node — needs exactly 2 outgoing edges (see: sagex syntax decision-edge).",
        "conditions[].operator is a comparison such as '==' or '!=' (see the web condition editor for the full set).",
        "conditions[].fieldSource / valueSource: 'output' (a {{...}} reference) or 'manual' (a literal).",
        "trueLabel / falseLabel list the target node ids taken on each branch.",
    ],
    "edge": [
        "Connection between two nodes. Required: id, source, target.",
        "type ('smoothstep') and style are UI hints — safe to keep as-is.",
    ],
    "decision-edge": [
        "The two required edges out of a decision node.",
        "sourceHandle 'true'/'false' is mandatory; label and coloured style match the web app.",
    ],
}

_SYNTAX_KINDS = " | ".join(_SYNTAX_SNIPPETS)


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


@app.command("syntax")
def syntax_cmd(
    kind: str = typer.Argument(..., help=f"Snippet to print: {_SYNTAX_KINDS}"),
) -> None:
    """Print a ready-to-copy JSON snippet for a workflow node or edge.

    Paste the output into your workflow file's 'nodes' or 'edges' array,
    then replace the TODO placeholders with real ids and values.

    Run `sagex list script` / `sagex list vault` to find the ids you need.
    """
    snippet = _SYNTAX_SNIPPETS.get(kind)
    if snippet is None:
        typer.echo(f"✗ Unknown kind '{kind}'. Choose one of: {_SYNTAX_KINDS}")
        raise typer.Exit(code=1)
    for line in _SYNTAX_NOTES.get(kind, []):
        render.note(f"# {line}")
    typer.echo(json.dumps(snippet, indent=2))


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


# ---------------------------------------------------------------------------
# Credential (key) input helpers. Secrets are entered interactively (hidden) or
# read from an existing file; they are NEVER accepted as flag values (which would
# leak into shell history / ps) and are NEVER written to disk by us.
# ---------------------------------------------------------------------------

_KEY_TYPES = {
    "username_password": "username_password", "up": "username_password",
    "user": "username_password", "password": "username_password",
    "ssh_key": "ssh_key", "ssh": "ssh_key", "key": "ssh_key",
    "certificate": "certificate", "cert": "certificate",
}
_KEY_TYPE_CHOICES = "username_password | ssh_key | certificate"


def _normalize_key_type(value: str) -> str:
    """Map a user-supplied --type (incl. aliases like 'ssh'/'cert') to a server value."""
    ctype = _KEY_TYPES.get(value.strip().lower())
    if not ctype:
        typer.echo(f"✗ --type must be one of: {_KEY_TYPE_CHOICES}.")
        raise typer.Exit(code=1)
    return ctype


def _prompt_secret(label: str, *, confirm: bool) -> str:
    """Prompt for a single-line secret with hidden input (optionally confirmed)."""
    return typer.prompt(label, hide_input=True, confirmation_prompt=confirm)


def _prompt_optional_secret(label: str) -> str:
    """Prompt for an optional single-line secret (hidden); '' if left blank."""
    return typer.prompt(label, hide_input=True, default="", show_default=False)


def _read_secret_file(path_str: str) -> str:
    """Read a multi-line secret (key/cert) from an existing file. Never written back."""
    p = Path(path_str).expanduser()
    if not p.is_file():
        typer.echo(f"✗ No such file: {p}")
        raise typer.Exit(code=1)
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        typer.echo(f"✗ Couldn't read {p}: {exc}")
        raise typer.Exit(code=1)


def _paste_secret(label: str) -> str:
    """Read a multi-line secret pasted into the terminal, ended with EOF."""
    typer.echo(f"  Paste the {label}, then press Ctrl-D (Ctrl-Z then Enter on Windows) on a blank line:")
    return sys.stdin.read()


def _acquire_multiline(label: str, file_opt: str | None, *, paste_when_none: bool) -> str | None:
    """Get a multi-line secret from a file path, from '-' (paste), or by paste.

    file_opt: a path, or '-' to paste, or None. When None: paste if paste_when_none
    (the create flow), else return None (the update flow — leave the field unchanged).
    """
    if file_opt == "-":
        content = _paste_secret(label)
    elif file_opt:
        content = _read_secret_file(file_opt)
    elif paste_when_none:
        content = _paste_secret(label)
    else:
        return None
    if not content.strip():
        typer.echo(f"✗ {label} is empty.")
        raise typer.Exit(code=1)
    return content


@show_app.command("workflow")
def show_workflow_cmd(
    ref: str = typer.Argument(..., help="Workflow name or id."),
    json_out: bool = typer.Option(False, "--json", help="Print the raw record as JSON."),
) -> None:
    """Show a workflow's details (including its nodes and edges). Use `sagex render` for the flow tree."""
    client = _client_or_exit()
    wf = _resolve_or_exit(resources.resolve_workflow, client, ref, "workflow")
    render.print_json(wf) if json_out else render.workflow(wf)


@render_app.command("workflow")
def render_workflow_cmd(
    ref: str = typer.Argument(None, help="Workflow name or id (omit when using --file)."),
    file: str = typer.Option(None, "--file", "-f", help="Render a local file: a path, or a name under <workspace>/workflows/."),
) -> None:
    """Render a workflow's flow as boxed nodes + edges. Works on a local file too (--file)."""
    if file:
        workspace = Path(config.workspace_dir())
        try:
            path = pusher.resolve_workflow_file(workspace, file)
        except pusher.WorkflowFileNotFound as exc:
            typer.echo(f"No workflow file for '{exc.ref}'. Looked at:")
            for p in exc.looked:
                typer.echo(f"  {p}")
            raise typer.Exit(code=1)
        try:
            wf = pusher.load_doc(path)
        except ValueError as exc:
            typer.echo(f"✗ {exc}")
            raise typer.Exit(code=1)
    else:
        if not ref:
            typer.echo("Give a workflow name/id, or pass --file <path-or-name>.")
            raise typer.Exit(code=1)
        client = _client_or_exit()
        wf = _resolve_or_exit(resources.resolve_workflow, client, ref, "workflow")
    render.workflow_graph(wf)


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


def _list_or_exit(fn, client, **kwargs):
    """Call a list_*_full function and return items, or exit on ApiError."""
    try:
        return fn(client, **kwargs)
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)


@list_app.command("workflow")
def list_workflow_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """List all workflows."""
    client = _client_or_exit()
    items = _list_or_exit(resources.list_workflows_full, client)
    render.print_json(render.slim_items(items, "workflow")) if json_out else render.list_workflows(items)


@list_app.command("script")
def list_script_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """List all scripts."""
    client = _client_or_exit()
    items = _list_or_exit(resources.list_scripts_full, client)
    render.print_json(render.slim_items(items, "script")) if json_out else render.list_scripts(items)


@list_app.command("run")
def list_run_cmd(
    limit: int = typer.Option(20, "--limit", help="Max runs to show (newest first)."),
    json_out: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """List recent runs, newest first."""
    client = _client_or_exit()
    items = _list_or_exit(resources.list_runs_full, client, limit=limit)
    render.print_json(render.slim_items(items, "run")) if json_out else render.list_runs(items)


@list_app.command("trigger")
def list_trigger_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """List all configured triggers."""
    client = _client_or_exit()
    items = _list_or_exit(resources.list_triggers_full, client)
    render.print_json(render.slim_items(items, "trigger")) if json_out else render.list_triggers(items)


@list_app.command("key")
def list_key_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """List all vault credentials (no secrets shown)."""
    client = _client_or_exit()
    items = _list_or_exit(resources.list_credentials_full, client)
    render.print_json(render.slim_items(items, "credential")) if json_out else render.list_credentials(items)


@list_app.command("server")
def list_server_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """List all vault servers."""
    client = _client_or_exit()
    items = _list_or_exit(resources.list_servers_full, client)
    render.print_json(render.slim_items(items, "server")) if json_out else render.list_servers(items)


@list_app.command("vault")
def list_vault_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print raw JSON."),
) -> None:
    """List all vaults."""
    client = _client_or_exit()
    items = _list_or_exit(resources.list_vaults_full, client)
    render.print_json(render.slim_vaults(items)) if json_out else render.list_vaults(items)


def _wrote(paths) -> None:
    """Report the files a copy command wrote (one dim line each)."""
    for p in paths:
        render.note(f"  wrote {p}")


@copy_app.command("workflow")
def copy_workflow_cmd(
    ref: str = typer.Argument(None, help="Workflow name or id (omit with --all)."),
    all_: bool = typer.Option(False, "--all", help="Copy every workflow."),
) -> None:
    """Copy a workflow (or all) as JSON to <workspace>/workflows/."""
    client = _client_or_exit()
    workspace = Path(config.workspace_dir())
    if all_:
        try:
            paths = copier.copy_all_workflows(client, workspace)
        except ApiError as exc:
            typer.echo(f"✗ {exc.message}"); raise typer.Exit(code=1)
        _wrote(paths)
        render.note(f"Copied {len(paths)} workflow(s) to {workspace / 'workflows'}")
        return
    if not ref:
        typer.echo("Give a workflow name/id, or pass --all."); raise typer.Exit(code=1)
    wf = _resolve_or_exit(resources.resolve_workflow, client, ref, "workflow")
    _wrote([copier.copy_workflow(workspace, wf)])


@copy_app.command("script")
def copy_script_cmd(
    ref: str = typer.Argument(None, help="Script name or id (omit with --all)."),
    all_: bool = typer.Option(False, "--all", help="Copy every script."),
) -> None:
    """Copy a script's code (or all) to <workspace>/scripts/."""
    client = _client_or_exit()
    workspace = Path(config.workspace_dir())
    if all_:
        try:
            paths = copier.copy_all_scripts(client, workspace)
        except ApiError as exc:
            typer.echo(f"✗ {exc.message}"); raise typer.Exit(code=1)
        _wrote(paths)
        render.note(f"Copied {len(paths)} script(s) to {workspace / 'scripts'}")
        return
    if not ref:
        typer.echo("Give a script name/id, or pass --all."); raise typer.Exit(code=1)
    meta = _resolve_or_exit(resources.resolve_script, client, ref, "script")
    try:
        content = resources.get_script_content(client, meta["id"])
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}"); raise typer.Exit(code=1)
    _wrote([copier.copy_script(workspace, meta, content)])


@copy_app.command("run")
def copy_run_cmd(
    ref: str = typer.Argument(None, help="Run id/prefix (omit when using --since)."),
    since: str = typer.Option(
        None, "--since",
        help="Copy all runs in a window instead of one: 1day | 7days | 1month | all.",
    ),
) -> None:
    """Copy a run's metadata + logs to <workspace>/runs/ (one folder per run)."""
    client = _client_or_exit()
    workspace = Path(config.workspace_dir())

    if since is not None:
        if since != "all" and since not in copier.WINDOWS:
            typer.echo("--since must be one of: 1day, 7days, 1month, all."); raise typer.Exit(code=1)
        try:
            runs = copier.runs_in_window(resources.list_runs_full(client, limit=None), since)
        except ApiError as exc:
            typer.echo(f"✗ {exc.message}"); raise typer.Exit(code=1)
        if not runs:
            render.note(f"No runs in the last {since}."); return
        render.note(f"Copying {len(runs)} run(s)…")
        total = 0
        for r in runs:
            try:
                paths = copier.copy_run(client, workspace, r)
            except ApiError as exc:
                typer.echo(f"✗ {r.get('id')}: {exc.message}"); continue
            _wrote(paths); total += len(paths)
        render.note(f"Copied {len(runs)} run(s) to {workspace / 'runs'}")
        return

    if not ref:
        typer.echo("Give a run id/prefix, or pass --since <window>."); raise typer.Exit(code=1)
    run = _resolve_or_exit(resources.resolve_run, client, ref, "run")
    try:
        _wrote(copier.copy_run(client, workspace, run))
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}"); raise typer.Exit(code=1)


@copy_app.command("key")
def copy_key_cmd(
    ref: str = typer.Argument(None, help="(unused)"),
) -> None:
    """Keys/credentials cannot be copied — secrets never touch local disk."""
    typer.echo("Keys can't be copied — secrets are never written to disk.")
    typer.echo("To view a secret value interactively, use:  sagex show key <ref> --reveal")
    raise typer.Exit(code=1)


@new_app.command("workflow")
def new_workflow_cmd(
    name: str = typer.Argument(..., help="Workflow display name."),
    output: str = typer.Option(None, "--output", "-o", help="Output path (default: <workspace>/workflows/<name>.json)."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if the file already exists."),
) -> None:
    """Create a template workflow JSON file in your workspace, ready to edit and push."""
    from sagex.copier import safe_name

    workspace = Path(config.workspace_dir())
    if output:
        dest = Path(output)
    else:
        stem = safe_name(name, "workflow")
        dest = workspace / "workflows" / f"{stem}.json"

    if dest.exists() and not force:
        typer.echo(f"✗ {dest} already exists. Pass --force to overwrite, or choose a different name.")
        raise typer.Exit(code=1)

    doc = dict(_WORKFLOW_TEMPLATE)
    doc["name"] = name

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, indent=2), encoding="utf-8")

    typer.echo(f"✓ Created {dest}")
    render.note("  Edit the file (fill in node ids, scriptId, vaultDetails, etc.)")
    render.note(f"  Push when ready:  sagex push workflow \"{dest}\"")
    render.note("  See the '_doc' field in the file for a full field reference.")


@new_app.command("script")
def new_script_cmd(
    name: str = typer.Argument(..., help="Script name (letters, numbers, _ and - only)."),
    language: str = typer.Option(None, "--language", "--lang", help="Language, e.g. shell, python, powershell (inferred from --output's extension if omitted)."),
    output: str = typer.Option(None, "--output", "-o", help="Output path (default: <workspace>/scripts/<name>.<ext>)."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if the file already exists."),
) -> None:
    """Create a starter script file with a header explaining how params & secrets reach it."""
    workspace = Path(config.workspace_dir())

    lang = language or (pusher.infer_script_language(Path(output)) if output else None)
    if not lang:
        typer.echo("Give a --language (e.g. shell, python, powershell), or an --output path with a known extension.")
        raise typer.Exit(code=1)

    try:
        filename, content = pusher.script_scaffold(name, lang)
    except ValueError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(code=1)

    dest = Path(output) if output else workspace / "scripts" / filename
    if dest.exists() and not force:
        typer.echo(f"✗ {dest} already exists. Pass --force to overwrite, or choose a different name.")
        raise typer.Exit(code=1)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")

    typer.echo(f"✓ Created {dest}")
    render.note("  Read the header — it explains how parameters and secrets reach the script.")
    render.note(f"  Push when ready:  sagex push script \"{dest}\"")


@create_app.command("vault")
def create_vault_cmd(
    name: str = typer.Argument(..., help="Vault name (must be unique among your vaults)."),
    description: str = typer.Option("", "--description", "-d", help="Optional description."),
) -> None:
    """Create a new vault — a container for servers and credentials."""
    client = _client_or_exit()

    # Name is unique per owner; check first (the server's duplicate error shape varies).
    try:
        existing = resources.find_vault_by_name(client, name)
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)
    if existing:
        typer.echo(f"✗ You already have a vault named '{name}' (id {existing.get('id')}).")
        typer.echo(f"  To change it, run:  sagex update vault \"{name}\" --description \"…\"")
        raise typer.Exit(code=1)

    try:
        created = resources.create_vault(client, {"name": name, "description": description})
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Created vault '{created.get('name')}' (id {created.get('id')})")


@update_app.command("vault")
def update_vault_cmd(
    ref: str = typer.Argument(..., help="Vault name or id."),
    name: str = typer.Option(None, "--name", help="New name for the vault."),
    description: str = typer.Option(None, "--description", "-d", help="New description."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Update a vault's name and/or description."""
    if name is None and description is None:
        typer.echo("Nothing to update — pass --name and/or --description.")
        raise typer.Exit(code=1)

    client = _client_or_exit()
    vault = _resolve_or_exit(resources.resolve_vault, client, ref, "vault")

    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if description is not None:
        payload["description"] = description

    # Guard a rename against colliding with another of your vaults.
    if name is not None and name.strip().lower() != str(vault.get("name") or "").lower():
        clash = resources.find_vault_by_name(client, name)
        if clash and str(clash.get("id")) != str(vault.get("id")):
            typer.echo(f"✗ You already have another vault named '{name}' (id {clash.get('id')}).")
            raise typer.Exit(code=1)

    typer.echo(f"Updating vault '{vault.get('name')}' (id {str(vault.get('id'))[:8]}…)")
    if not yes and not typer.confirm("  Apply these changes?"):
        typer.echo("Aborted — nothing changed.")
        raise typer.Exit(code=1)

    try:
        updated = resources.update_vault(client, vault["id"], payload)
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Updated vault '{updated.get('name')}' (id {updated.get('id')})")


@create_app.command("server")
def create_server_cmd(
    name: str = typer.Argument(..., help="Server name (unique within the vault)."),
    vault: str = typer.Option(..., "--vault", help="Vault name or id to create the server in (required)."),
    host: str = typer.Option(..., "--host", help="Hostname or IP address (required)."),
    method: str = typer.Option(..., "--method", "-m", help="Connection method: ssh or winrm (required)."),
    port: int = typer.Option(None, "--port", "-p", help="Port (default: 22 for ssh, 5985 for winrm)."),
    key: str = typer.Option(None, "--key", "-k", help="Credential (key) name or id to attach — must be in the same vault."),
) -> None:
    """Create a vault server (SSH or WinRM). Optionally attach a credential with --key."""
    method = method.lower()
    if method not in ("ssh", "winrm"):
        typer.echo("✗ --method must be 'ssh' or 'winrm'.")
        raise typer.Exit(code=1)
    if port is None:
        port = 22 if method == "ssh" else 5985
    elif not 1 <= port <= 65535:
        typer.echo("✗ --port must be between 1 and 65535.")
        raise typer.Exit(code=1)

    client = _client_or_exit()
    v = _resolve_or_exit(resources.resolve_vault, client, vault, "vault")

    # Server name is unique within its vault — check the vault's existing servers.
    for s in (v.get("servers") or []):
        if str(s.get("name") or "").lower() == name.strip().lower():
            typer.echo(f"✗ Vault '{v.get('name')}' already has a server named '{name}' (id {s.get('id')}).")
            raise typer.Exit(code=1)

    payload = {
        "vault": v["id"],
        "name": name,
        "host": host,
        "connection_method": method,
        "port": port,
    }

    cred = None
    if key:
        cred = resources.find_credential_in_vault(v, key)
        if not cred:
            typer.echo(f"✗ No credential '{key}' in vault '{v.get('name')}'.")
            typer.echo(f"  A server's key must live in the same vault. See:  sagex show vault \"{v.get('name')}\"")
            raise typer.Exit(code=1)
        payload["credential"] = cred["id"]

    try:
        created = resources.create_server(client, payload)
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)

    typer.echo(f"✓ Created server '{created.get('name')}' (id {created.get('id')}) in vault '{v.get('name')}'")
    attached = f" · key: {cred.get('name')}" if cred else " · no key attached"
    render.note(f"  {method} · {host}:{port}{attached}")


@create_app.command("key")
def create_key_cmd(
    name: str = typer.Argument(..., help="Credential name (unique within the vault)."),
    vault: str = typer.Option(..., "--vault", help="Vault name or id to create the key in (required)."),
    type_: str = typer.Option(..., "--type", "-t", help=f"Credential type: {_KEY_TYPE_CHOICES} (aliases: ssh, cert)."),
    username: str = typer.Option(None, "--username", "-u", help="Username (username_password type; prompted if omitted)."),
    key_file: str = typer.Option(None, "--key-file", help="SSH private key file path, or - to paste (ssh_key type)."),
    cert_file: str = typer.Option(None, "--cert-file", help="Certificate PEM file path, or - to paste (certificate type)."),
) -> None:
    """Create a vault credential (key). Secrets are entered securely — never via flags."""
    ctype = _normalize_key_type(type_)

    # Reject flags that don't match the chosen type (fast, before any network/prompt).
    if username is not None and ctype != "username_password":
        typer.echo("✗ --username only applies to a username_password key.")
        raise typer.Exit(code=1)
    if key_file is not None and ctype != "ssh_key":
        typer.echo("✗ --key-file only applies to an ssh_key key.")
        raise typer.Exit(code=1)
    if cert_file is not None and ctype != "certificate":
        typer.echo("✗ --cert-file only applies to a certificate key.")
        raise typer.Exit(code=1)

    client = _client_or_exit()
    v = _resolve_or_exit(resources.resolve_vault, client, vault, "vault")

    # Name is unique within the vault — check BEFORE prompting for any secret.
    if resources.find_credential_in_vault(v, name):
        typer.echo(f"✗ Vault '{v.get('name')}' already has a key named '{name}'.")
        raise typer.Exit(code=1)

    payload = {"vault": v["id"], "name": name, "credential_type": ctype}
    if ctype == "username_password":
        payload["username"] = username if username is not None else typer.prompt("Username")
        payload["password"] = _prompt_secret("Password", confirm=True)
    elif ctype == "ssh_key":
        payload["ssh_key"] = _acquire_multiline("SSH private key", key_file, paste_when_none=True)
        passphrase = _prompt_optional_secret("Key passphrase (leave blank if none)")
        if passphrase:
            payload["key_passphrase"] = passphrase
    else:  # certificate
        payload["cert_pem"] = _acquire_multiline("certificate PEM", cert_file, paste_when_none=True)

    try:
        created = resources.create_credential(client, payload)
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Created key '{created.get('name')}' (id {created.get('id')}, type {ctype}) in vault '{v.get('name')}'")


@update_app.command("server")
def update_server_cmd(
    ref: str = typer.Argument(..., help="Server name or id."),
    name: str = typer.Option(None, "--name", help="New server name (unique within its vault)."),
    host: str = typer.Option(None, "--host", help="New hostname or IP address."),
    method: str = typer.Option(None, "--method", "-m", help="New connection method: ssh or winrm."),
    port: int = typer.Option(None, "--port", "-p", help="New port (1-65535)."),
    key: str = typer.Option(None, "--key", "-k", help="Attach a credential (name or id) from the same vault."),
    unlink_key: bool = typer.Option(False, "--unlink-key", help="Detach the currently attached credential."),
    vault: str = typer.Option(None, "--vault", help="Scope the lookup to this vault (name or id) — use when the same server name exists in multiple vaults."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Update a vault server's fields and/or its attached credential."""
    if key and unlink_key:
        typer.echo("✗ Use either --key or --unlink-key, not both.")
        raise typer.Exit(code=1)
    if name is None and host is None and method is None and port is None and not key and not unlink_key:
        typer.echo("Nothing to update — pass --name/--host/--method/--port/--key/--unlink-key.")
        raise typer.Exit(code=1)

    if method is not None:
        method = method.lower()
        if method not in ("ssh", "winrm"):
            typer.echo("✗ --method must be 'ssh' or 'winrm'.")
            raise typer.Exit(code=1)
    if port is not None and not 1 <= port <= 65535:
        typer.echo("✗ --port must be between 1 and 65535.")
        raise typer.Exit(code=1)

    client = _client_or_exit()

    # --vault scopes the lookup to one vault (names are unique there); otherwise
    # resolve globally, which reports an ambiguity if the name spans vaults.
    vault_detail = None
    if vault:
        vault_detail = _resolve_or_exit(resources.resolve_vault, client, vault, "vault")
        server = resources.find_server_in_vault(vault_detail, ref)
        if not server:
            typer.echo(f"✗ No server '{ref}' in vault '{vault_detail.get('name')}'.")
            raise typer.Exit(code=1)
    else:
        server = _resolve_or_exit(resources.resolve_server, client, ref, "server")

    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if host is not None:
        payload["host"] = host
    if method is not None:
        payload["connection_method"] = method
    if port is not None:
        payload["port"] = port

    # A rename or a key change both need the vault detail (its servers + credentials
    # lists). Reuse the one fetched for --vault, else fetch by the server's vault id.
    if (name is not None or key) and vault_detail is None:
        vault_detail = _resolve_or_exit(resources.resolve_vault, client, server.get("vault"), "vault")

    if name is not None:
        for s in (vault_detail.get("servers") or []):
            if str(s.get("id")) != str(server.get("id")) and str(s.get("name") or "").lower() == name.strip().lower():
                typer.echo(f"✗ Vault '{vault_detail.get('name')}' already has another server named '{name}'.")
                raise typer.Exit(code=1)

    cred = None
    if key:
        cred = resources.find_credential_in_vault(vault_detail, key)
        if not cred:
            typer.echo(f"✗ No credential '{key}' in this server's vault '{vault_detail.get('name')}'.")
            typer.echo(f"  A server's key must live in the same vault. See:  sagex show vault \"{vault_detail.get('name')}\"")
            raise typer.Exit(code=1)
        payload["credential"] = cred["id"]
    elif unlink_key:
        payload["credential"] = None

    typer.echo(f"Updating server '{server.get('name')}' (id {str(server.get('id'))[:8]}…)")
    if not yes and not typer.confirm("  Apply these changes?"):
        typer.echo("Aborted — nothing changed.")
        raise typer.Exit(code=1)

    try:
        updated = resources.update_server(client, server["id"], payload)
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)

    typer.echo(f"✓ Updated server '{updated.get('name')}' (id {updated.get('id')})")


@update_app.command("key")
def update_key_cmd(
    ref: str = typer.Argument(..., help="Credential (key) name or id."),
    vault: str = typer.Option(None, "--vault", help="Scope the lookup to this vault (name or id) — for a name shared across vaults."),
    name: str = typer.Option(None, "--name", help="New name for the key."),
    username: str = typer.Option(None, "--username", "-u", help="New username (username_password keys)."),
    set_password: bool = typer.Option(False, "--set-password", help="Prompt for a new password (hidden)."),
    key_file: str = typer.Option(None, "--key-file", help="Replace the SSH key from this file path, or - to paste."),
    set_passphrase: bool = typer.Option(False, "--set-passphrase", help="Prompt for a new key passphrase (blank clears it)."),
    cert_file: str = typer.Option(None, "--cert-file", help="Replace the certificate PEM from this file path, or - to paste."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Update a vault credential — rename or rotate a secret. Secrets entered securely."""
    if not (name is not None or username is not None or set_password
            or set_passphrase or key_file is not None or cert_file is not None):
        typer.echo("Nothing to update — pass --name/--username/--set-password/--key-file/--set-passphrase/--cert-file.")
        raise typer.Exit(code=1)

    client = _client_or_exit()

    # --vault scopes the lookup (names are unique per vault); else resolve globally.
    if vault:
        vd = _resolve_or_exit(resources.resolve_vault, client, vault, "vault")
        cred = resources.find_credential_in_vault(vd, ref)
        if not cred:
            typer.echo(f"✗ No key '{ref}' in vault '{vd.get('name')}'.")
            raise typer.Exit(code=1)
    else:
        cred = _resolve_or_exit(resources.resolve_credential, client, ref, "key")
        vd = None

    ctype = cred.get("credential_type")

    # Only allow changes that match the key's type (update can't change the type).
    if (username is not None or set_password) and ctype != "username_password":
        typer.echo(f"✗ This key is type '{ctype}'; --username/--set-password only apply to username_password.")
        raise typer.Exit(code=1)
    if (key_file is not None or set_passphrase) and ctype != "ssh_key":
        typer.echo(f"✗ This key is type '{ctype}'; --key-file/--set-passphrase only apply to ssh_key.")
        raise typer.Exit(code=1)
    if cert_file is not None and ctype != "certificate":
        typer.echo(f"✗ This key is type '{ctype}'; --cert-file only applies to certificate.")
        raise typer.Exit(code=1)

    # Rename clash guard within the same vault.
    if name is not None:
        if vd is None:
            vd = _resolve_or_exit(resources.resolve_vault, client, cred.get("vault"), "vault")
        clash = resources.find_credential_in_vault(vd, name)
        if clash and str(clash.get("id")) != str(cred.get("id")):
            typer.echo(f"✗ Vault '{vd.get('name')}' already has another key named '{name}'.")
            raise typer.Exit(code=1)

    # Confirm the intended changes BEFORE entering any secret value.
    changes = []
    if name is not None:        changes.append(f"rename to '{name}'")
    if username is not None:     changes.append("set username")
    if set_password:            changes.append("new password")
    if key_file is not None:     changes.append("replace SSH key")
    if set_passphrase:          changes.append("new passphrase")
    if cert_file is not None:    changes.append("replace certificate")
    typer.echo(f"Updating key '{cred.get('name')}' (id {str(cred.get('id'))[:8]}…): {', '.join(changes)}")
    if not yes and not typer.confirm("  Proceed?"):
        typer.echo("Aborted — nothing changed.")
        raise typer.Exit(code=1)

    # Collect values (prompts / files) only after confirmation.
    payload: dict = {}
    if name is not None:
        payload["name"] = name
    if username is not None:
        payload["username"] = username
    if set_password:
        payload["password"] = _prompt_secret("New password", confirm=True)
    if key_file is not None:
        payload["ssh_key"] = _acquire_multiline("SSH private key", key_file, paste_when_none=False)
    if set_passphrase:
        payload["key_passphrase"] = _prompt_optional_secret("New key passphrase (blank to clear)")
    if cert_file is not None:
        payload["cert_pem"] = _acquire_multiline("certificate PEM", cert_file, paste_when_none=False)

    try:
        updated = resources.update_credential(client, cred["id"], payload)
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Updated key '{updated.get('name')}' (id {updated.get('id')})")


@push_app.command("workflow")
def push_workflow_cmd(
    ref: str = typer.Argument(..., help="Workflow file path, or a name under <workspace>/workflows/."),
    new: bool = typer.Option(False, "--new", help="Force-create a new workflow even if the file has an id."),
    name: str = typer.Option(None, "--name", help="Override the workflow name (useful with --new)."),
    yes: bool = typer.Option(False, "--yes", help="Skip the update confirmation prompt."),
) -> None:
    """Push a workflow file to the server — creates if new, updates if it carries an id."""
    client = _client_or_exit()
    workspace = Path(config.workspace_dir())

    try:
        path = pusher.resolve_workflow_file(workspace, ref)
    except pusher.WorkflowFileNotFound as exc:
        typer.echo(f"No workflow file for '{exc.ref}'. Looked at:")
        for p in exc.looked:
            typer.echo(f"  {p}")
        raise typer.Exit(code=1)

    try:
        doc = pusher.load_doc(path)
    except ValueError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(code=1)

    problems = pusher.validate_workflow_doc(doc)
    if problems:
        typer.echo(f"✗ {path} is not a valid workflow:")
        for p in problems:
            typer.echo(f"  - {p}")
        raise typer.Exit(code=1)

    payload = pusher.workflow_payload(doc, name)
    wid = None if new else doc.get("id")
    label = payload["name"]

    if wid:
        # UPDATE — fetch the server copy first (confirms it's ours + stale-write guard).
        try:
            server = resources.get_workflow_detail(client, wid)
        except ApiError as exc:
            if exc.status == 404:
                typer.echo(f"No workflow {wid} on the server (deleted, or not yours).")
                typer.echo("To create a fresh copy instead, re-run with --new.")
            else:
                typer.echo(f"✗ {exc.message}")
            raise typer.Exit(code=1)

        typer.echo(f"Updating '{label}' (id {str(wid)[:8]}…)")
        if pusher.server_is_newer(server.get("modified_at"), doc.get("modified_at")):
            typer.echo(f"  ⚠ server copy is newer (server {server.get('modified_at')} "
                       f"vs local {doc.get('modified_at')}) — pushing overwrites it.")
        if not yes and not typer.confirm("  Overwrite the server copy?"):
            typer.echo("Aborted — nothing pushed.")
            raise typer.Exit(code=1)

        try:
            updated = resources.update_workflow(client, wid, payload)
        except ApiError as exc:
            typer.echo(f"✗ {exc.message}")
            raise typer.Exit(code=1)
        pusher.sync_meta_back(path, doc, updated)
        typer.echo(f"✓ Updated '{updated.get('name')}' (id {updated.get('id')})")
        return

    # CREATE
    typer.echo(f"Creating '{label}'…")
    try:
        created = resources.create_workflow(client, payload)
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)
    pusher.sync_meta_back(path, doc, created)
    typer.echo(f"✓ Created '{created.get('name')}' (id {created.get('id')})")
    render.note(f"  wrote id back to {path}")


@push_app.command("script")
def push_script_cmd(
    ref: str = typer.Argument(..., help="Script file path, or a name under <workspace>/scripts/."),
    name: str = typer.Option(None, "--name", help="Override the script name (letters, numbers, _ and - only)."),
    language: str = typer.Option(None, "--language", "--lang", help="Override the language (else inferred from the file extension)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the update confirmation prompt."),
) -> None:
    """Push a script file — creates it, or updates the existing one after confirming.

    A script's code can't carry a server id, so this matches by filename: it looks
    for one of yours with the same name and, if found, offers to update it (bumping
    its version); otherwise it creates a new script.
    """
    client = _client_or_exit()
    workspace = Path(config.workspace_dir())

    try:
        path = pusher.resolve_script_file(workspace, ref)
    except pusher.ScriptFileNotFound as exc:
        if exc.matches:
            typer.echo(f"'{exc.ref}' matches more than one file — pass the full name:")
            for p in exc.matches:
                typer.echo(f"  {p.name}")
        else:
            typer.echo(f"No script file for '{exc.ref}'. Looked at:")
            for p in exc.looked:
                typer.echo(f"  {p}")
        raise typer.Exit(code=1)

    try:
        spec = pusher.script_push_spec(path, name, language)
    except ValueError as exc:
        typer.echo(f"✗ {exc}")
        raise typer.Exit(code=1)

    # Check-then-confirm: is there already a script of ours with this filename?
    try:
        existing = resources.find_script_by_name(client, spec["server_name"])
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)

    if existing:
        sid = existing.get("id")
        typer.echo(f"'{spec['server_name']}' already exists (id {sid}, v{existing.get('version')}, "
                   f"updated {existing.get('updated_at')}).")

        # Preview what the update would overwrite (best-effort; don't block on a fetch error).
        try:
            server_code = (resources.get_script_content(client, sid) or {}).get("content") or ""
        except ApiError as exc:
            server_code = None
            render.note(f"  (couldn't fetch the current version to diff: {exc.message})")

        if server_code is not None and not render.script_diff(spec["server_name"], server_code, spec["content"]):
            typer.echo("Local file matches the server copy — nothing to update.")
            return

        if not yes and not typer.confirm("  Update it? This overwrites the code and bumps the version."):
            typer.echo("Aborted — nothing pushed.")
            raise typer.Exit(code=1)
        try:
            updated = resources.update_script(client, sid, spec["content"])
        except ApiError as exc:
            typer.echo(f"✗ {exc.message}")
            raise typer.Exit(code=1)
        typer.echo(f"✓ Updated '{updated.get('name')}' (id {updated.get('id')}, now v{updated.get('version')})")
        return

    # CREATE — no existing script to clobber (server rejects a duplicate pathname).
    typer.echo(f"Creating '{spec['server_name']}' (language: {spec['language']})…")
    try:
        created = resources.create_script(client, {
            "name": spec["name"], "language": spec["language"], "content": spec["content"],
        })
    except ApiError as exc:
        typer.echo(f"✗ {exc.message}")
        raise typer.Exit(code=1)
    typer.echo(f"✓ Created '{created.get('name')}' (id {created.get('id')})")


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
