"""Build step: bake .env into the package as sagex/_env.py.

Package metadata lives in pyproject.toml — this file exists only for this step.
Like a Vite build, .env values are compiled in at build time, because an installed
wheel has no .env next to it. So everything baked here ships PUBLICLY inside the
package: never put secrets in .env.

.env.example is the schema: exactly the keys it lists are baked. A key missing from
.env fails the build; an extra key is ignored (not published), with a warning.

Runs on every build (sdist, wheel, editable install). When the wheel is built from
an sdist there's no .env, but the sdist already carries the baked _env.py — keep it.
"""

import sys
from pathlib import Path

from setuptools import setup

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
SCHEMA_FILE = ROOT / ".env.example"
TARGET = ROOT / "sagex" / "_env.py"


def parse_env(path: Path) -> dict:
    """Minimal .env reader: KEY=VALUE lines; '#' comments; optional quotes/`export`."""
    values = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


def bake() -> None:
    if not ENV_FILE.is_file():
        if TARGET.is_file():
            return
        sys.exit("sagex build: no .env file. Create one from the template:  cp .env.example .env")

    keys = list(parse_env(SCHEMA_FILE))
    values = parse_env(ENV_FILE)

    missing = [k for k in keys if not values.get(k)]
    if missing:
        sys.exit(f"sagex build: set these in .env: {', '.join(missing)}")
    bad_urls = [k for k in keys if k.endswith("_URL") and not values[k].startswith(("https://", "http://"))]
    if bad_urls:
        sys.exit(f"sagex build: these must be http(s) URLs: {', '.join(bad_urls)}")
    extra = sorted(set(values) - set(keys))
    if extra:
        print(f"sagex build: ignoring .env keys not declared in .env.example: {', '.join(extra)}",
              file=sys.stderr)

    lines = [
        "# Generated from .env by setup.py at build time. Do not edit or commit.",
        "# Ships publicly inside the package: never secrets.",
        "",
        "VALUES = {",
        *(f"    {k!r}: {values[k]!r}," for k in keys),
        "}",
        "",
    ]
    content = "\n".join(lines)
    if not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != content:
        TARGET.write_text(content, encoding="utf-8")


bake()
setup()
