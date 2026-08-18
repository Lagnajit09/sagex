"""Mock data for the Resources tree.

For now these are hardcoded lists so we can build the UI. Later, each of these
will be replaced by real responses from the Autosage API — and because all the
data lives here, the rest of the app won't need to change when that happens.
"""

# Workflow names (left panel > Workflows).
WORKFLOWS = [
    "Deploy Production",
    "Database Backup",
    "Health Check",
    "User Sync",
    "Cleanup Logs",
]

# Script file names (left panel > Scripts).
SCRIPTS = [
    "backup_postgres.sh",
    "deploy.py",
    "healthcheck.ps1",
]

# Vault resources (left panel > Vault).
VAULT = [
    "prod-01 (server)",
    "prod-02 (server)",
    "aws-key (credential)",
]

# The 5 most recent runs, newest first: (status, name, when).
# Only runs carry a status — a workflow/script definition does not.
RECENT_RUNS = [
    ("running", "Health Check",      "just now"),
    ("success", "Deploy Production", "2m ago"),
    ("success", "Database Backup",   "1h ago"),
    ("failed",  "User Sync",         "3h ago"),
    ("success", "Cleanup Logs",      "6h ago"),
]
