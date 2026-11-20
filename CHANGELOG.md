# Changelog

All notable changes to sagex are recorded here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Sage**, your Autosage assistant, in the terminal app: type a question (no `!`) and the reply streams into the transcript as Markdown. Esc stops a reply.
- Ask about the command you just ran: the next question carries its output (tail only, with obvious secrets masked; never for `--reveal` or environment dumps).
- `SAGEX_AI_URL` to point sagex at an Autobot running on its own port (defaults to `<api url>/api/ai`).

- Sage looks things up for you: it can run read-only sagex commands (`list`, `show`, `render`, `trigger invoke`, `syntax`, `workspace show`) and read any command's help. Every call appears in the transcript as a card showing the exact command and its output; click a finished card to expand it. Esc stops it.

- Sage can make changes for you (run, create, update, rename, delete, push, copy…), always with your say-so: each one appears as an approval card with a one-line description of what it will do and the exact command. Press `y` to run it once, `a` to let Sage run that command on that resource again without asking for the rest of the session, or `n` to decline. Deletes always ask and carry a warning. A workflow run draws its live graph inside the card.
- `/permissions` lists what you've let Sage run without asking, and `/permissions revoke <n>` (or `revoke all`) takes it back. The line under the prompt shows how many are allowed.
- A blank line under the prompt, so it doesn't sit against the footer.
- Sage can work with files in your workspace: read them, write them (it shows the new file, or a diff when it replaces one, before you approve) and delete them (always asks). Only files in the workspace or the terminal's current folder, and never ones that usually hold secrets (`.env`, keys, certificates, `.git`, `.ssh`…). "Create a script that…" now ends with the script written and pushed.
- Sage can run shell commands on your machine (checking a version, installing a tool) in your current shell, after you approve each one. It can't type into prompts, see your SageCode key or print environment variables, and commands that delete things, need admin rights or download-and-run code always ask, with a warning.
- Sage edits files in place: it changes just the lines that need changing (shown as a diff) instead of rewriting the whole file. Allowing Sage to write a file for the session covers its edits too.
- Long conversations stay fast: output from earlier replies is shortened in what Sage remembers, and it re-reads a file when it needs the exact text.
- Sage plans better: it says up front when something isn't possible with its tools instead of trying anyway, and it knows where your OS, shell and folders are.

### Changed

- **The workspace is the one place for local resources.** Every relative path a command takes (`push`, `render --file`, `new --output`) is relative to the workspace, never the folder you're in, so it doesn't matter where you start sagex or run a command. The terminal app opens in the workspace and moves there when you change it (F3). Sage works only there: its sagex commands, files and shell commands all run in the workspace, and paths it passes to sagex can't leave it. (Secret files for `--secret-file` are still read from wherever you point.)

### Fixed

- `push script <name>` could upload the wrong file: a same-named file in the current folder (such as a leftover `sagex new script` template) won over the one in `<workspace>/scripts/`. Push now looks only in the workspace, lists both files if `<workspace>/x` and `<workspace>/scripts/x` differ, and always prints which local file it used.
- "No value for 'NAME'" now says it's the script's `{{NAME}}` input with no default.
- When Sage hits its step limit for one request it ends with a short summary and a note that it stopped, instead of a half-written tool call.

Sage uses your personal LLM key from Autobot settings when one is set; otherwise it shares Autobot's daily free allowance. Commands that sign in, take a secret or print one (`auth`, keys, trigger secrets, `--reveal`) are never available to Sage, and a run that needs a secret typed at a hidden prompt is handed back to you to run with `!`.

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

[1.1.0]: https://github.com/Lagnajit09/sagex/releases/tag/v1.1.0
[1.0.0]: https://github.com/Lagnajit09/sagex/releases/tag/v1.0.0
