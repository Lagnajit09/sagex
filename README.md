# sagex

**Autosage in your terminal.** Build, run and watch your [Autosage](https://autosagex.web.app) workflows, scripts, vaults and triggers from a full-screen terminal app or one-shot commands. Everything the web app does, minus the mouse.

[![PyPI](https://img.shields.io/pypi/v/sagex-cli)](https://pypi.org/project/sagex-cli/)
[![Python](https://img.shields.io/pypi/pyversions/sagex-cli)](https://pypi.org/project/sagex-cli/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](https://github.com/Lagnajit09/sagex/blob/main/LICENSE)

```
$ sagex list workflow
┌──────────────────────┬──────────────────────────────────────┬──────────┐
│ name                 │ id                                   │ modified │
├──────────────────────┼──────────────────────────────────────┼──────────┤
│ Nightly Disk Cleanup │ 46a2ba05-3bed-40b6-a967-2a3b0dc2b657 │ 2d ago   │
│ Deploy               │ 3ee4dd1f-8865-4144-bcec-5aaa36df0b30 │ 5d ago   │
└──────────────────────┴──────────────────────────────────────┴──────────┘
```

> **Early access.** sagex is invite-only for now. Your account needs an API key: [request one here](https://autosagex.web.app/contact).

## Install

**macOS / Linux**

```sh
curl -LsSf https://raw.githubusercontent.com/Lagnajit09/sagex/main/scripts/install.sh | sh
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/Lagnajit09/sagex/main/scripts/install.ps1 | iex
```

The installer uses [uv](https://docs.astral.sh/uv/) to install sagex as an isolated tool, and fetches Python 3.11+ for you if needed. Set `SAGEX_VERSION=1.0.0` before running it to pin a version.

**Already have a Python tool manager?** Any of these works (Python 3.11 or newer):

```sh
uv tool install sagex-cli
pipx install sagex-cli
pip install sagex-cli
```

## Quick start

```sh
sagex auth login              # paste your API key (stored in your OS keychain)
sagex                         # open the terminal app
sagex list workflow           # or run single commands
sagex run workflow "Deploy"   # run a workflow and follow it live, node by node
```

## The terminal app

Run `sagex` with no arguments to open it.

- **Left:** your **Resources**: Workflows, Scripts, Vault, recent Runs (with ✓ / ✗ / ⟳ status) and Triggers (● on / ○ off). Press **F3** to switch to your local **Workspace** folder instead.
- **Right:** a terminal-style transcript. Start a line with `!` to run any command, such as `!sagex run workflow Deploy` or `!git status`. Output streams in live, and workflow runs draw a live node graph.

| Key | Action |
|---|---|
| `!command` | Run a shell or sagex command |
| `Esc` | Stop the running command |
| `↑` / `↓` | Command history |
| `F2` | Switch shell (PowerShell / cmd / WSL on Windows, bash / sh elsewhere) |
| `F3` | Switch between server resources and the local workspace |
| `F5` | Refresh |
| `Ctrl+Q` | Quit |

Chatting with Autobot from the terminal is coming in **sagex v2**. Until then, use Autobot in the [web app](https://autosagex.web.app/ai/autobot).

## Commands

Resources can be referred to by **name or id**. An id prefix works for runs. Most read commands take `--json` for scripting. Run `sagex <group> --help` for every option.

| Group | Commands | What it does |
|---|---|---|
| `auth` | `login` · `status` · `logout` | Store, check or remove your API key |
| `list` | `workflow` · `script` · `run` · `trigger` · `vault` · `server` · `key` | Compact tables of your resources |
| `show` | `workflow` · `script` · `run` · `trigger` · `vault` · `server` · `key` | One resource in detail; `show run --logs` prints node output, `show key --reveal` shows secrets after confirming |
| `render` | `workflow` | Draw a workflow as a flow tree (from the server, or a local file with `--file`) |
| `run` | `script` · `workflow` | Run now and stream output live. `Ctrl+C` stops it. Pass inputs with `-p NAME=VALUE` |
| `trigger` | `create` · `fire` · `poll` · `watch` · `invoke` · `enable` · `disable` · `regenerate` | HTTP webhooks and cron schedules: set up, fire, follow and rotate |
| `new` | `workflow` · `script` | Scaffold a local file, ready to edit |
| `push` | `workflow` · `script` | Create on the server from a local file, or update after confirming (scripts show a diff) |
| `copy` | `workflow` · `script` · `run` | Download to your workspace (`--all`, or `copy run --since 7days`) |
| `create` | `vault` · `server` · `key` | Create vault resources |
| `update` | `vault` · `server` · `key` | Change them, or rotate a key's secret |
| `rename` | `workflow` · `script` · `vault` · `server` · `key` | Rename in place |
| `delete` | `workflow` · `script` · `vault` · `server` · `key` · `trigger` | Delete, after confirming |
| `workspace` | `set` · `show` | Choose the local folder sagex reads from and writes to |
| `syntax` | `trigger` · `script` · `email` · `decision` · `edge` · `decision-edge` | Print a copy-paste JSON snippet for a workflow node or edge |

### A typical loop

```sh
sagex new script disk_check --lang shell     # scripts/disk_check.sh
sagex push script disk_check                 # create it on the server
sagex run script disk_check.sh -s web-1      # run it on a vault server

sagex new workflow "Disk Check"              # workflows/Disk_Check.json
sagex syntax script                          # snippet for an action node
sagex render workflow --file "Disk Check"    # preview the flow locally
sagex push workflow "Disk Check"
sagex trigger create "Disk Check" -t schedule --cron "0 2 * * *"
sagex trigger watch "Disk Check"             # wait for the next firing, then follow it
```

## Your workspace

sagex keeps local files in one folder: your **workspace**. It defaults to the current directory; set it with `sagex workspace set <path>`.

```
workflows/               workflow JSON (sagex new / copy / push)
scripts/                 script files  (sagex new / copy / push)
runs/                    run metadata + logs (sagex copy run)
logs/script/<id>/        saved by sagex run script
logs/workflow/<run_id>/  saved by sagex run workflow / trigger fire
```

## Security

- Your API key is stored in the **OS keychain** (Windows Credential Manager, macOS Keychain or Linux Secret Service), never in a plain file.
- Secrets are **never accepted as command-line flags**, so they never show up in shell history. Passwords, SSH keys and passphrases are typed at a hidden prompt or read from a file you point to.
- Vault credentials **can't be copied** to disk, and `show key` hides secret values unless you pass `--reveal` and confirm.
- An HTTP trigger's secret is shown **once**, when it's created or rotated.

## Configuration

| Setting | Purpose |
|---|---|
| `SAGEX_API_KEY` | Use this API key instead of the keychain. Useful for CI or machines without a keychain |
| `SAGEX_API_URL` | Point sagex at a different Autosage server |
| `SAGEX_DEBUG=1` | Add connection details to network errors |
| `~/.sagex/config.json` | Saved preferences (workspace, shell) |

## Update or uninstall

```sh
uv tool upgrade sagex-cli        # or: pipx upgrade sagex-cli / pip install -U sagex-cli
```

To uninstall, run `sagex auth logout` first (it removes your key from the keychain), then `uv tool uninstall sagex-cli` (or `pipx uninstall` / `pip uninstall`).

## Roadmap

- **v1.1:** see your sagex sessions on your Autosage profile.
- **v2:** Autobot in the terminal: chat, generate, run and fix, with your last command's output as context.

## Links

- Autosage: https://autosagex.web.app
- Documentation: https://autosagexdocs.web.app
- Issues: https://github.com/Lagnajit09/sagex/issues
- Changelog: https://github.com/Lagnajit09/sagex/blob/main/CHANGELOG.md

## License

[MIT](https://github.com/Lagnajit09/sagex/blob/main/LICENSE)
