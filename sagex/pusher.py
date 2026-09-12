"""Read local resource files and push them to the server (create/update).

The file-side of `sagex push`: locate a local workflow file (by path or by name
under the workspace), parse + validate it, and shape the payload the API expects.
The actual POST/PUT go through sagex.api.resources; the CLI layer owns the prompts
(confirm on update, stale-write guard). No terminal formatting or prompts here.

Create-vs-update is decided by the `id` field: a file carrying a server id is an
update to that workflow, one without is a brand-new create. (Workflow names are
not unique server-side, so the id is the only reliable key.)
"""

import json
import re
from datetime import datetime
from pathlib import Path

from sagex.copier import safe_name

# Fields the workflow write endpoints accept; everything else (timestamps, id) is
# server-owned and read-only.
_PAYLOAD_FIELDS = ("name", "description", "nodes", "edges")

# Reverse of the server's LANGUAGE_MAP (scripts/serializers.py): file extension ->
# language keyword the create endpoint accepts. '.yml' is an alias the server lacks
# (it stores yaml as '.yaml'), so a foo.yml pushes as foo.yaml — see script_push_spec.
_EXT_TO_LANGUAGE = {
    "js": "javascript", "ts": "typescript", "py": "python", "java": "java",
    "cpp": "cpp", "c": "c", "cs": "csharp", "go": "go", "rs": "rust",
    "rb": "ruby", "php": "php", "swift": "swift", "kt": "kotlin",
    "ps1": "powershell", "sh": "shell", "sql": "sql", "html": "html",
    "css": "css", "json": "json", "xml": "xml", "yaml": "yaml", "yml": "yaml",
    "md": "markdown",
}

# language -> the extension the server will actually store the file under. Lets us
# predict the server-side filename so create-vs-update matching is reliable.
_LANGUAGE_EXT = {
    "javascript": "js", "typescript": "ts", "python": "py", "java": "java",
    "cpp": "cpp", "c": "c", "csharp": "cs", "go": "go", "rust": "rs",
    "ruby": "rb", "php": "php", "swift": "swift", "kotlin": "kt",
    "powershell": "ps1", "shell": "sh", "bash": "sh", "sql": "sql",
    "html": "html", "css": "css", "json": "json", "xml": "xml",
    "yaml": "yaml", "markdown": "md",
}

# The server's script-name rule (letters, numbers, _ and - only — no dots/spaces).
_SCRIPT_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class WorkflowFileNotFound(Exception):
    """No workflow file matched the given path-or-name."""

    def __init__(self, ref: str, looked: list[Path]) -> None:
        super().__init__(f"No workflow file for '{ref}'.")
        self.ref = ref
        self.looked = looked


def resolve_workflow_file(workspace: Path, ref: str) -> Path:
    """Find a workflow file by explicit path, or by name under <ws>/workflows/.

    Tries, in order: the ref as a path; then <ws>/workflows/{ref}, {ref}.json, and
    the sanitized {safe}.json (so a display name like "Nightly Disk Cleanup" finds
    the copied "Nightly_Disk_Cleanup.json").
    """
    direct = Path(ref)
    if direct.is_file():
        return direct

    folder = workspace / "workflows"
    stem = ref[:-5] if ref.lower().endswith(".json") else ref
    candidates = [
        folder / ref,
        folder / f"{ref}.json",
        folder / f"{safe_name(stem, stem)}.json",
    ]
    seen = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
        if c.is_file():
            return c
    raise WorkflowFileNotFound(ref, [direct, *seen])


def load_doc(path: Path) -> dict:
    """Parse a JSON workflow file. Raises ValueError with a clear message on bad JSON.

    Reads as utf-8-sig so a UTF-8 BOM (which Windows editors / PowerShell often add)
    is transparently stripped; it's a no-op for BOM-free files like the ones `copy` writes.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"Couldn't read {path}: {exc}")


def validate_workflow_doc(doc: dict) -> list[str]:
    """Return a list of problems (empty = valid). Mirrors the web Import dialog's checks."""
    if not isinstance(doc, dict):
        return ["file is not a JSON object"]

    problems: list[str] = []
    if not str(doc.get("name") or "").strip():
        problems.append("missing 'name'")

    nodes = doc.get("nodes")
    if not isinstance(nodes, list):
        problems.append("'nodes' must be a list")
    else:
        for i, n in enumerate(nodes):
            if not isinstance(n, dict) or not all(k in n for k in ("id", "type", "position")):
                problems.append(f"node[{i}] must have id, type, position")

    edges = doc.get("edges")
    if not isinstance(edges, list):
        problems.append("'edges' must be a list")
    else:
        for i, e in enumerate(edges):
            if not isinstance(e, dict) or not all(k in e for k in ("id", "source", "target")):
                problems.append(f"edge[{i}] must have id, source, target")

    return problems


def workflow_payload(doc: dict, name_override: str | None = None) -> dict:
    """Build the create/update payload: only the server-writable fields."""
    payload = {k: doc.get(k) for k in _PAYLOAD_FIELDS if k in doc}
    payload.setdefault("description", "")
    payload.setdefault("nodes", [])
    payload.setdefault("edges", [])
    if name_override:
        payload["name"] = name_override
    return payload


def server_is_newer(server_modified, local_modified) -> bool:
    """True if the server's modified_at is strictly newer than the local file's.

    Used for the stale-write guard: warns before an update would clobber changes
    made (e.g. in the web UI) since the file was copied. False if either is unparseable.
    """
    try:
        return datetime.fromisoformat(server_modified) > datetime.fromisoformat(local_modified)
    except (ValueError, TypeError):
        return False


def sync_meta_back(path: Path, doc: dict, server_record: dict) -> None:
    """Write the server's id/timestamps back into the local file after a push.

    Keeps the user's nodes/edges untouched; only refreshes server-owned metadata so
    the next push updates (not duplicates) and the stale-write guard stays accurate.
    """
    for key in ("id", "created_at", "modified_at"):
        if server_record.get(key) is not None:
            doc[key] = server_record[key]
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Scripts. Unlike workflows, a script's code file can't carry a server id, so
# `push script` identifies its target by filename and lets the CLI decide
# create-vs-update (check-then-confirm). The language is inferred from the file
# extension; the server owns id/version/owner/content_type.
# ---------------------------------------------------------------------------


class ScriptFileNotFound(Exception):
    """No script file matched the given path-or-name (or a bare name matched several)."""

    def __init__(self, ref: str, looked: list[Path], matches: list[Path] | None = None) -> None:
        super().__init__(f"No script file for '{ref}'.")
        self.ref = ref
        self.looked = looked
        self.matches = matches or []          # >1 hit when a bare name is ambiguous


def resolve_script_file(workspace: Path, ref: str) -> Path:
    """Find a script file by explicit path, or by name under <ws>/scripts/.

    Tries: the ref as a path; then <ws>/scripts/{ref}. If ref has no extension,
    globs <ws>/scripts/{ref}.* and returns it only when exactly one file matches
    (raises with the candidates when several do).
    """
    direct = Path(ref)
    if direct.is_file():
        return direct

    folder = workspace / "scripts"
    exact = folder / ref
    looked = [direct, exact]
    if exact.is_file():
        return exact

    if "." not in ref and folder.is_dir():
        matches = sorted(p for p in folder.glob(f"{ref}.*") if p.is_file())
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ScriptFileNotFound(ref, looked, matches=matches)

    raise ScriptFileNotFound(ref, looked)


def script_push_spec(
    path: Path, name_override: str | None = None, language_override: str | None = None
) -> dict:
    """Turn a local file into a create payload + the name the server will store it as.

    Returns {name, language, content, server_name}. `name` is the bare stem sent to
    the create endpoint; `server_name` is "{name}.{ext}" — what the server stores and
    what `find_script_by_name` matches on. Raises ValueError (with a fix hint) when
    the extension is unmapped, the name is illegal, or the file is empty.
    """
    try:
        content = path.read_text(encoding="utf-8-sig")   # strip a BOM if present
    except OSError as exc:
        raise ValueError(f"Couldn't read {path}: {exc}")

    name = name_override or path.stem
    ext = path.suffix.lstrip(".").lower()

    if language_override:
        language = language_override.lower()
    else:
        language = _EXT_TO_LANGUAGE.get(ext)
        if not language:
            langs = ", ".join(sorted(set(_EXT_TO_LANGUAGE.values())))
            shown = f".{ext}" if ext else "(no extension)"
            raise ValueError(
                f"Can't tell the language from '{shown}'. Pass --language <lang> "
                f"(one of: {langs})."
            )

    if language not in _LANGUAGE_EXT:
        raise ValueError(
            f"Unsupported language '{language}'. Supported: "
            f"{', '.join(sorted(_LANGUAGE_EXT))}."
        )

    if not _SCRIPT_NAME_RE.match(name):
        raise ValueError(
            f"Script name '{name}' isn't allowed — the server accepts only letters, "
            f"numbers, '_' and '-' (no dots or spaces). Pass --name <name> to override."
        )

    if not content.strip():
        raise ValueError("Script content is empty — nothing to push.")

    server_name = f"{name}.{_LANGUAGE_EXT[language]}"
    return {"name": name, "language": language, "content": content, "server_name": server_name}
