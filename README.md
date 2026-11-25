# sagex

**Autosage in your terminal.** Build, run and watch your [Autosage](https://autosagex.web.app) workflows, scripts, vaults and triggers from a full-screen terminal app or one-shot commands. Everything the web app does, minus the mouse, plus **Sage**: an assistant that looks things up, writes and pushes scripts, runs workflows and fixes what failed, asking you before it changes anything.

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

You need an [Autosage account](https://autosagex.web.app/signup) (free).

## Install

**macOS / Linux**

```sh
curl -LsSf https://raw.githubusercontent.com/Lagnajit09/sagex/main/scripts/install.sh | sh
```

**Windows (PowerShell)**

```powershell
irm https://raw.githubusercontent.com/Lagnajit09/sagex/main/scripts/install.ps1 | iex
```

The installer uses [uv](https://docs.astral.sh/uv/) to install sagex as an isolated tool, and fetches Python 3.11+ for you if needed. Set `SAGEX_VERSION=2.1.0` before running it to pin a version.

**Already have a Python tool manager?** Any of these works (Python 3.11 or newer):

```sh
uv tool install sagex-cli
pipx install sagex-cli
pip install sagex-cli
```

## Quick start

```sh
sagex auth login              # approve this device in your browser
sagex                         # open the terminal app, then just ask Sage
sagex list workflow           # or run single commands
sagex run workflow "Deploy"   # run a workflow and follow it live, node by node
```

## The terminal app

Run `sagex` with no arguments to open it.

- **Left:** your **Resources**: Workflows, Scripts, Vault, recent Runs (with ✓ / ✗ / ⟳ status) and Triggers (● on / ○ off). Press **F3** to switch to your local **Workspace** folder instead.
- **Right:** a terminal-style transcript. Start a line with `!` to run any command, such as `!sagex run workflow Deploy` or `!git status`. Output streams in live, and workflow runs draw a live node graph. Anything else goes to **Sage**.
- **Bottom left:** **Sage Today**: today's requests and tokens, the model Sage uses, and the free requests left.

| Key | Action |
|---|---|
| `!command` | Run a shell or sagex command |
| *anything else* | Ask Sage |
| `Esc` | Stop the running command or Sage's reply |
| `↑` / `↓` | Command history |
| `F2` | Switch shell (PowerShell / cmd / WSL on Windows, bash / sh elsewhere) |
| `F3` | Switch between server resources and the local workspace |
| `F5` | Refresh |
| `Ctrl+Q` | Quit |

## Sage

Type a question without `!` and Sage, your Autosage assistant, answers in the transcript. Ask "why did that fail?" right after a command and it sees that command's output.

- **It looks things up by itself.** Read-only sagex commands and command help run straight away, each shown as a card with the exact command and its output.
- **It asks before changing anything.** Runs, pushes, creates, deletes, file writes and shell commands appear as an approval card with a one-line summary and the exact command: `y` once, `a` for the rest of the session, `n` no. Deletes always ask. `/permissions` lists and revokes what you've allowed.
- **It works in your workspace.** It reads, writes and edits files there (showing a diff first) and runs shell commands in your current shell. Never outside the workspace, never files that usually hold secrets.
- **It never touches secrets.** Signing in, vault and model keys, trigger secrets and `--reveal` aren't available to it; a run that needs a typed secret is handed back to you.

| Command | Action |
|---|---|
| `/sessions` | Your saved conversations; `/sessions <n>` reopens one, `/sessions delete <n>` deletes one |
| `/new` | Start a fresh conversation |
| `/model` | The models Sage can use; `/model <n or name>` or `/model free` switches |
| `/usage` | Requests and tokens today, last 7 days and all time |
| `/permissions` | What Sage may do without asking; `/permissions revoke <n>` or `revoke all` |

**Your own model.** Sage answers with Autosage's free models (a daily allowance shared with Autobot on the web) unless you pick one of your own:

```sh
sagex create model "My Claude" --provider anthropic --model claude-sonnet-4-5 --use
```

The API key is typed at a hidden prompt (or read with `--key-file`), stored encrypted on Autosage and never shown again. Providers: Gemini, Groq, OpenRouter, Anthropic, OpenAI, Azure OpenAI, NVIDIA NIM, or any LiteLLM-compatible endpoint (`--base-url`). `sagex use model <name>` or `--free` switches; the choice also applies to Autobot on the web.

## Commands

Resources can be referred to by **name or id**. An id prefix works for runs. Most read commands take `--json` for scripting. Run `sagex <group> --help` for every option.

| Group | Commands | What it does |
|---|---|---|
| `auth` | `login` · `status` · `logout` | Sign in with your browser (or `--with-token`), see who you're signed in as, sign out |
| `list` | `workflow` · `script` · `run` · `trigger` · `vault` · `server` · `key` · `model` | Compact tables of your resources |
| `show` | `workflow` · `script` · `run` · `trigger` · `vault` · `server` · `key` · `model` | One resource in detail; `show run --logs` prints node output, `show key --reveal` shows secrets after confirming |
| `render` | `workflow` | Draw a workflow as a flow tree (from the server, or a local file with `--file`) |
| `run` | `script` · `workflow` | Run now and stream output live. `Ctrl+C` stops it. Pass inputs with `-p NAME=VALUE` |
| `rerun` | `<run>` | Run a workflow again with an earlier run's inputs (`-p` changes one; secrets are asked for again) and follow it live |
| `watch` | `<run>` | Follow a run that's already going, started anywhere. `Ctrl+C` stops watching; the run keeps going |
| `cancel` | `<run>` | Cancel a queued or running workflow run, after confirming |
| `trigger` | `create` · `fire` · `poll` · `watch` · `invoke` · `enable` · `disable` · `regenerate` | HTTP webhooks and cron schedules: set up, fire, follow and rotate |
| `new` | `workflow` · `script` | Scaffold a local file, ready to edit |
| `push` | `workflow` · `script` | Create on the server from a local file, or update after confirming (scripts show a diff) |
| `copy` | `workflow` · `script` · `run` | Download to your workspace (`--all`, or `copy run --since 7days`) |
| `create` | `vault` · `server` · `key` · `model` | Create vault resources, or add your own model |
| `update` | `vault` · `server` · `key` · `model` | Change them, or rotate a key's secret / a model's API key |
| `rename` | `workflow` · `script` · `vault` · `server` · `key` · `model` | Rename in place |
| `move` | `key` | Move a key to another vault (`--to`); refused while a server uses it, and lists the workflows that do |
| `delete` | `workflow` · `script` · `vault` · `server` · `key` · `model` · `trigger` | Delete, after confirming |
| `use` | `model` | Choose the model Sage (and Autobot on the web) answers with, or `--free` |
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
logs/script/<id>/        saved by sagex run script
logs/workflow/<run_id>/  run metadata + logs: saved by sagex run workflow / rerun / watch /
                         trigger fire, and by sagex copy run
```

## Signing in

`sagex auth login` prints a one-time code and opens Autosage in your browser. Check the code matches, approve, and you're in. On a server or over SSH, open the link on any device instead.

Each sign-in is a **session**. Your [Autosage profile](https://autosagex.web.app/profile#sagecode) lists them all, with the device, SageCode version, when it was last active, and how many commands and runs it has started, and can revoke any of them. Sessions unused for 90 days expire. `sagex auth logout` signs this machine out.

**CI and scripts:** create an **access token** on your profile and set it as `SAGEX_API_KEY`, or pipe it in with `sagex auth login --with-token`.

## Security

- Your session key is stored in the **OS keychain** (Windows Credential Manager, macOS Keychain or Linux Secret Service), never in a plain file.
- Approving a sign-in, creating tokens and revoking sessions all need you signed in to Autosage in the browser; a SageCode key can't do any of them.
- Secrets are **never accepted as command-line flags**, so they never show up in shell history. Passwords, SSH keys and passphrases are typed at a hidden prompt or read from a file you point to.
- Vault credentials **can't be copied** to disk, and `show key` hides secret values unless you pass `--reveal` and confirm.
- An HTTP trigger's secret is shown **once**, when it's created or rotated.
- Model API keys are write-only: stored encrypted on Autosage, never shown or copied back.
- Sage runs nothing on its own server: every command, file change and shell command runs on your machine, in your workspace, through the same approval checks, and Sage's replies are cleaned of terminal escape sequences and unsafe links before they're shown.

## Configuration

| Setting | Purpose |
|---|---|
| `SAGEX_API_KEY` | Use this access token instead of the keychain. For CI or machines without a keychain; create one on your Autosage profile |
| `SAGEX_API_URL` | Point sagex at a different Autosage server |
| `SAGEX_AI_URL` | Point Sage at an Autobot on its own address (default: `<api url>/api/ai`) |
| `SAGEX_DEBUG=1` | Add connection details to network errors |
| `~/.sagex/config.json` | Saved preferences (workspace, shell) |

## Update or uninstall

```sh
uv tool upgrade sagex-cli        # or: pipx upgrade sagex-cli / pip install -U sagex-cli
```

To uninstall, run `sagex auth logout` first (it removes your key from the keychain), then `uv tool uninstall sagex-cli` (or `pipx uninstall` / `pip uninstall`).

## Development

```sh
git clone https://github.com/Lagnajit09/sagex && cd sagex
cp .env.example .env          # then fill in the server URLs
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

`.env` holds build settings (server URLs). They're baked into the package when it's built, so they ship publicly: never put secrets there. After editing `.env`, re-run `pip install -e .`.

## Roadmap

- **v1.1 (shipped):** sign in with your browser, and see and revoke every session on your Autosage profile.
- **v2 (shipped):** Sage in the terminal app: ask, look up, change things with your approval, files and shell in your workspace, saved conversations, your own model, usage.
- **Next:** Sage remembers your preferences across conversations, reusable skills, and connecting your own tools.

## Links

- Autosage: https://autosagex.web.app
- Documentation: https://autosagexdocs.web.app
- Issues: https://github.com/Lagnajit09/sagex/issues
- Changelog: https://github.com/Lagnajit09/sagex/blob/main/CHANGELOG.md

## License

[MIT](https://github.com/Lagnajit09/sagex/blob/main/LICENSE)
