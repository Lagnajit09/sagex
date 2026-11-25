# Changelog

All notable changes to sagex are recorded here. Versions follow [Semantic Versioning](https://semver.org/).

## [2.1.0] - 2026-11-25

### Added

- `sagex rerun <run>` runs a workflow again with an earlier run's inputs and follows it live, like `run workflow`. `-p NAME=VALUE` changes a value. Secrets the earlier run was given are asked for again at a hidden prompt, because Autosage keeps them only masked. It starts an ordinary manual run of the workflow as it is now.
- `sagex watch <run>` follows a run that's already going, whether it was started from the web app, a trigger, Sage or another terminal. The graph starts with the nodes already done. `Ctrl+C` stops watching and leaves the run going. Its logs are saved when it ends.
- `sagex cancel <run>` cancels a queued or running workflow run, after confirming (`--yes` skips that).
- Sage can use all three. Each one shows an approval card first, and cancelling says that the remaining nodes won't run.
- `sagex move key <key> --to <vault>` moves a key to another of your vaults. Its secrets move with it, unseen. It's refused while a server uses the key, because a server's key has to be in the server's own vault; the message names those servers and the command to fix each one. Workflows that use the key are listed before you confirm, because they fail until their nodes are pointed at the new vault.

### Changed

- `sagex copy run` saves to `logs/workflow/<run_id>/`, the folder a run you followed live is saved to, instead of `runs/<workflow>__<id>/`. Folders already in `runs/` are left as they are.
- When a run you were following is detached, the hint points to `sagex watch <run>` to pick it up again.

## [2.0.1] - 2026-11-22

### Fixed

- Saving a setting (your shell, your workspace) also saved the Autosage server address, which then overrode the address in every later install, so sagex could keep talking to an old or local server after an upgrade. Only settings you changed are saved now; a saved address that matches the built-in one is dropped the next time settings are saved. `auth login` and `auth status` say which server you're using whenever it isn't the built-in one.

## [2.0.0] - 2026-11-22

**Meet Sage**, your Autosage assistant in the terminal app. Type a question without `!` and Sage answers, looks things up, and with your approval changes things for you: it runs sagex commands, writes and edits files in your workspace and runs shell commands. Conversations are saved, you can bring your own model, and you can see what you've used.

### Added

**Sage**

- Type a question (no `!`) and the reply streams into the transcript as Markdown. Esc stops it. Your next question carries the output of the command you just ran (the end of it, with obvious secrets masked; never for `--reveal` or environment dumps), so "why did that fail?" just works.
- Sage looks things up by itself: it runs read-only sagex commands (`list`, `show`, `render`, `trigger invoke`, `syntax`, `workspace show`) and reads any command's help. Every call shows in the transcript as a card with the exact command and its output; click a finished card to expand it.
- Sage makes changes only with your say-so. Runs, creates, updates, renames, deletes, pushes and copies each appear as an approval card with a one-line description and the exact command: `y` runs it once, `a` allows that command on that resource for the rest of the session, `n` declines. Deletes always ask and carry a warning. A workflow run draws its live graph inside the card.
- `/permissions` lists what you've allowed and `/permissions revoke <n>` (or `revoke all`) takes it back. The line under the prompt shows how many are allowed.
- Files in your workspace: Sage reads them, writes them (showing the new file, or a diff, before you approve), edits just the lines that need changing, and deletes them (always asks). Never outside the workspace, and never files that usually hold secrets (`.env`, keys, certificates, `.git`, `.ssh`…). "Create a script that…" ends with the script written and pushed.
- Shell commands on your machine (check a version, install a tool), in your current shell, each one approved. Sage can't type into prompts, see your SageCode key or print environment variables; commands that delete things, need admin rights or download-and-run code always ask, with a warning.
- Sage plans before acting: it says up front when something isn't possible with its tools, knows your OS, shell and workspace, and stops with a short summary if it reaches its step limit for one request.
- Works with models that don't support tool calling: calls a model writes out as text (in any of the common formats) run like normal ones, behind the same approval cards, and never show up in the reply, and neither do a model's private reasoning (`<think>…`) or stray template tokens.

**Conversations**

- Conversations with Sage are saved. `/sessions` lists your recent ones (when, device, workspace folder), `/sessions <n>` reopens one with Sage remembering it, `/sessions delete <n>` deletes one and `/new` starts fresh. A dropped connection picks the same conversation back up. Your latest 100 are kept. Long tool output and file contents are trimmed before saving, so your files' contents aren't stored.
- Long conversations stay fast and sharp: older tool output is shortened in what Sage remembers (it re-reads a file when it needs the exact text), and when a conversation grows long Sage summarizes its older part and carries on from the summary.

**Your own model**

- `sagex create model "<name>" --provider <provider> --model <model id>` adds a model with your own provider API key: Gemini, Groq, OpenRouter, Anthropic, OpenAI, Azure OpenAI, NVIDIA NIM or any LiteLLM-compatible endpoint. The key is typed at a hidden prompt or read from `--key-file`, never a flag; it's stored encrypted on Autosage and never shown again.
- `sagex use model <name>` (or `--free`) picks what Sage answers with; `list`, `show`, `update` (`--new-key` replaces the key), `rename` and `delete model` manage them. These are the same models as Autobot's Customize panel on the web, and the choice applies to both.
- In the terminal app, `/model` lists them with the one in use marked, and `/model <number or name>` (or `/model free`) switches. Sage can list your models and, with your approval, switch them, but never add one or change a key.

**Usage**

- A "Sage Today" block at the bottom of the left panel shows today's requests and tokens, the model in use and how many free requests are left, updated as Sage replies. It also says when your own model failed and Sage fell back to the free models.
- `/usage` adds the last 7 days and all time, requests per day, the models and devices you used, and a link to the Agent Dashboard on the web, which now has a SageCode tab. The free daily allowance is shared with Autobot on the web.

- `SAGEX_AI_URL` points sagex at an Autobot running on its own port (defaults to `<api url>/api/ai`).

### Changed

- **The workspace is the one place for local resources.** Every relative path a command takes (`push`, `render --file`, `new --output`) is relative to the workspace, never the folder you're in. The terminal app opens in the workspace and moves there when you change it (F3). Sage works only there. (Files for `--secret-file` are still read from wherever you point.)
- A blank line under the prompt, so it doesn't sit against the footer.

### Security

- Sage's replies can't control your terminal: escape sequences (colours, cursor moves, hidden hyperlinks, window titles) and text-direction tricks are removed before anything is shown, in replies and on tool cards.
- Only web (`http`, `https`) and `mailto` links in Sage's replies are clickable. Anything else (a program on your disk, a Windows settings page, a local file) is shown as plain text, and a link whose text names one website but points to another shows where it really goes.
- Commands that sign in, take a secret or print one (`auth`, vault keys, model keys, trigger secrets, `--reveal`) are never available to Sage; a run that needs a secret typed at a hidden prompt is handed back to you to run with `!`.

### Fixed

- `push script <name>` could upload the wrong file: a same-named file in the current folder (such as a leftover `sagex new script` template) won over the one in `<workspace>/scripts/`. Push now looks only in the workspace, lists both files if `<workspace>/x` and `<workspace>/scripts/x` differ, and always prints which file it used.
- "No value for 'NAME'" now says it's the script's `{{NAME}}` input with no default.
Sage uses your selected personal model when you've set one; otherwise it uses Autosage's free models and the daily free allowance shared with Autobot on the web.

## [1.1.0] - 2026-11-01

Open to every Autosage account: sign in with your browser, no invite needed. Every sign-in is a session you can see and revoke on your Autosage profile.

### Added

- `sagex auth login` signs in through your browser: it shows a one-time code, you check it matches and approve. Works over SSH too: open the link on any device (`--no-browser` just prints it).
- `sagex auth login --with-token` for CI and scripts: pipe in an access token created on your Autosage profile.
- `sagex auth status` shows the account, the session and its activity.
- Your Autosage profile lists every SageCode session (device, OS, version, last active, commands run, runs started) and can revoke any of them.
- A security email when SageCode signs in on a new device.

### Changed

- `sagex auth logout` also signs the session out on the server (access tokens are left working, as CI may share them). Logging in again replaces this machine's previous session.
- Sessions unused for 90 days expire; sign in again to continue. When a session stops working, sagex says why (expired, signed out or revoked).
- The project homepage is now https://sagecode.web.app.

### Fixed

- The terminal app's header reads "sagex" (it showed the internal class name).
- The welcome banner wraps on narrow terminals instead of being cut off.

## [1.0.0] - 2026-10-29

The first public release: everything the Autosage web app does, from your terminal. Autobot chat follows in v2.

### Terminal app

- Full-screen app (`sagex`): resources tree (workflows, scripts, vault, recent runs with status, triggers) beside a terminal-style transcript.
- Run any shell or sagex command with `!`, with live output, `Esc` to stop, and `↑`/`↓` history. Workflow runs draw a live node graph.
- Switch between server resources and a local workspace folder (`F3`), cycle shells (`F2`), refresh (`F5`).

### Commands

- `auth`: log in with an API key stored in the OS keychain; `status` and `logout`.
- `list` / `show`: workflows, scripts, runs (with `--logs`), triggers, vaults, servers and keys, with `--json` output.
- `render workflow`: draw a workflow as a flow tree, from the server or a local file.
- `run script` / `run workflow`: run now and follow live; `Ctrl+C` stops; logs saved to the workspace.
- `trigger`: create HTTP and schedule triggers, `fire`, `poll`, `watch` the next scheduled run, `invoke` cheat-sheet, `enable`/`disable`, `regenerate` secrets.
- `new` / `push` / `copy`: scaffold, upload and download workflows and scripts; copy run logs.
- `create` / `update` / `rename` / `delete`: vaults, servers and keys (and workflows, scripts and triggers where applicable).
- `syntax`: copy-paste JSON snippets for workflow nodes and edges.

### Security

- Secrets are never accepted as flags or written to disk; they're entered at hidden prompts or read from a file you choose.
- Crash reports never include local variables, so a key can't leak into a traceback.

### Configuration

- `SAGEX_API_KEY` (use a key without the keychain, e.g. in CI), `SAGEX_API_URL`, `SAGEX_DEBUG`.

[2.1.0]: https://github.com/Lagnajit09/sagex/releases/tag/v2.1.0
[2.0.1]: https://github.com/Lagnajit09/sagex/releases/tag/v2.0.1
[2.0.0]: https://github.com/Lagnajit09/sagex/releases/tag/v2.0.0
[1.1.0]: https://github.com/Lagnajit09/sagex/releases/tag/v1.1.0
[1.0.0]: https://github.com/Lagnajit09/sagex/releases/tag/v1.0.0
