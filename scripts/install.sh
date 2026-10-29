#!/bin/sh
# sagex installer for macOS and Linux.
#
#   curl -LsSf https://raw.githubusercontent.com/Lagnajit09/sagex/main/scripts/install.sh | sh
#
# Installs the sagex CLI as an isolated tool with uv (https://docs.astral.sh/uv/),
# installing uv first if it's missing. uv also fetches a suitable Python (3.11+) when
# none is available, so this works on a machine with no Python at all.
#
# Environment:
#   SAGEX_VERSION   install a specific version (e.g. 1.0.0) instead of the latest
set -eu

PACKAGE="sagex-cli"

say() { printf '%s\n' "$*"; }
fail() { printf 'sagex install: %s\n' "$*" >&2; exit 1; }

find_uv() {
    if command -v uv >/dev/null 2>&1; then
        command -v uv
        return 0
    fi
    # Where uv's own installer puts it, which may not be on PATH in this shell yet.
    for candidate in "${XDG_BIN_HOME:-}/uv" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

UV="$(find_uv || true)"
if [ -z "$UV" ]; then
    say "→ Installing uv (the Python tool installer sagex uses)…"
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        fail "need curl or wget to download uv."
    fi
    UV="$(find_uv)" || fail "uv was installed but couldn't be found. Open a new terminal and re-run this script."
fi

SPEC="$PACKAGE"
if [ -n "${SAGEX_VERSION:-}" ]; then
    SPEC="$PACKAGE==$SAGEX_VERSION"
fi

say "→ Installing $SPEC…"
"$UV" tool install --upgrade "$SPEC"

# Make sure uv's tool folder (where the `sagex` command lives) is on PATH for new shells.
"$UV" tool update-shell >/dev/null 2>&1 || true

say ""
say "✓ sagex is installed."
if ! command -v sagex >/dev/null 2>&1; then
    say "  Open a new terminal so the 'sagex' command is on your PATH."
fi
say ""
say "Next steps:"
say "  sagex auth login     # paste your API key"
say "  sagex                # open the terminal app"
say "  sagex --help         # every command"
