# Changelog

All notable changes to sagex are recorded here. Versions follow [Semantic Versioning](https://semver.org/).

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

[1.0.0]: https://github.com/Lagnajit09/sagex/releases/tag/v1.0.0
