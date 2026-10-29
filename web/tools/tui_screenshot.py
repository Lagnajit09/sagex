"""Regenerate web/assets/img/tui.svg: a real screenshot of the sagex terminal app.

The app is rendered headlessly with demo data, so no server, API key or real
account is involved (and no real resource names end up on the website).

    python web/tools/tui_screenshot.py        # from the repo root, sagex installed
"""

import asyncio
import shutil
import sys
from pathlib import Path

from sagex import app as app_module
from sagex import shell
from sagex.api import resources, store
from sagex.runview import WorkflowRunView
from sagex.widgets.message import ChatMessage
from sagex.widgets.run_graph import RunGraph

OUT = Path(__file__).resolve().parents[1] / "assets" / "img" / "tui.svg"
SIZE = (140, 36)

WORKFLOWS = ["Deploy Production", "Nightly Disk Cleanup", "Rotate TLS Certs", "DB Backup"]
SCRIPTS = ["build.sh", "migrate.py", "smoke_test.sh", "disk_cleanup.sh", "rotate_certs.ps1"]
VAULT = ["web-1 (server)", "win-db (server)", "deploy-key (credential)", "smtp (credential)"]
RUNS = [("success", "Deploy Production", "just now"), ("running", "Nightly Disk Cleanup", "1m ago"),
        ("failed", "Rotate TLS Certs", "2h ago"), ("success", "DB Backup", "5h ago")]
TRIGGERS = [("Nightly Disk Cleanup", "scheduler", True), ("Deploy Production", "http", True),
            ("DB Backup", "scheduler", False)]

# A finished run of "Deploy Production", drawn by the app's own RunGraph widget.
RUN = {
    "name": "Deploy Production",
    "nodes": [
        {"id": "t", "label": "Git push", "kind": "trigger"},
        {"id": "b", "label": "Build", "kind": "script"},
        {"id": "m", "label": "Migrate", "kind": "script"},
        {"id": "d", "label": "Healthy?", "kind": "decision"},
        {"id": "n", "label": "Notify team", "kind": "email"},
        {"id": "r", "label": "Roll back", "kind": "script"},
    ],
    "edges": [["t", "b", None], ["b", "m", None], ["m", "d", None],
              ["d", "n", "true"], ["d", "r", "false"]],
}
RUN_EVENTS = [
    {"t": "node", "id": "t", "status": "success"},
    {"t": "node", "id": "b", "status": "success", "exit_code": 0, "duration": 2.3},
    {"t": "node", "id": "m", "status": "success", "exit_code": 0, "duration": 4.1},
    {"t": "node", "id": "d", "status": "success", "branch": "true", "duration": 0.1},
    {"t": "node", "id": "n", "status": "success", "duration": 0.8},
    {"t": "node", "id": "r", "status": "skipped"},
    {"t": "run", "status": "success"},
]
RUN_OUTPUT = """▶ Deploy Production · 6 nodes
  VERSION=1.4.2
› Build · script
✓ Build · exit 0 · 2.3s
› Migrate · script
  applied 3 migrations
✓ Migrate · exit 0 · 4.1s
✓ success · 9.8s · run 46a2ba05"""


async def capture() -> str:
    store.get_key = lambda: "sgx_demo"                      # "logged in" → server tree
    app_module.build_client = lambda: None
    resources.list_workflows = lambda c: WORKFLOWS
    resources.list_scripts = lambda c: SCRIPTS
    resources.list_vault_resources = lambda c: VAULT
    resources.list_recent_runs = lambda c: RUNS
    resources.list_triggers = lambda c: TRIGGERS
    shell.ShellSession.prompt = property(lambda self: "bash · ~/infra")

    app = app_module.SagexApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(0.5)                              # let the tree loaders finish
        app.add_message('sagex run workflow "Deploy Production" -p VERSION=1.4.2', "command")
        view = WorkflowRunView.from_snapshot(RUN)
        graph = RunGraph(view)
        out = ChatMessage(RUN_OUTPUT, "cmd_output")
        app._mount_entry(graph)
        app._mount_entry(out)
        await pilot.pause()
        for event in RUN_EVENTS:
            graph.apply(event)
        await pilot.pause(0.3)
        return app.export_screenshot(title="sagex")


def main() -> None:
    svg = asyncio.run(capture())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(svg, encoding="utf-8")
    print(f"wrote {OUT} ({len(svg) // 1024} KB)")


if __name__ == "__main__":
    if not shutil.which("sagex") and "sagex" not in sys.modules:
        print("note: run with sagex installed (pip install -e .)", file=sys.stderr)
    main()
