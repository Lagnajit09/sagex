# sagex installer for Windows (PowerShell 5.1+).
#
#   irm https://raw.githubusercontent.com/Lagnajit09/sagex/main/scripts/install.ps1 | iex
#
# Installs the sagex CLI as an isolated tool with uv (https://docs.astral.sh/uv/),
# installing uv first if it's missing. uv also fetches a suitable Python (3.11+) when
# none is available, so this works on a machine with no Python at all.
#
# Environment:
#   SAGEX_VERSION   install a specific version (e.g. 1.0.0) instead of the latest
#
# Everything runs inside a function: `irm | iex` executes in the caller's scope, so
# top-level variables would leak into the user's session.

function Install-Sagex {
    $package = "sagex-cli"

    function Find-Uv {
        $cmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
        # Where uv's own installer puts it, which may not be on PATH in this session yet.
        foreach ($candidate in @(
            (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
            (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
        )) {
            if (Test-Path $candidate) { return $candidate }
        }
        return $null
    }

    $uv = Find-Uv
    if (-not $uv) {
        Write-Host "-> Installing uv (the Python tool installer sagex uses)..."
        Invoke-RestMethod https://astral.sh/uv/install.ps1 -ErrorAction Stop | Invoke-Expression
        $uv = Find-Uv
        if (-not $uv) {
            Write-Host "uv was installed but couldn't be found. Open a new terminal and re-run this script." -ForegroundColor Red
            return
        }
    }

    $spec = $package
    if ($env:SAGEX_VERSION) { $spec = "$package==$($env:SAGEX_VERSION)" }

    # uv reports progress on stderr; let it flow straight to the console and judge
    # success by the exit code (PowerShell 5.1 turns redirected stderr into errors).
    Write-Host "-> Installing $spec..."
    & $uv tool install --upgrade $spec
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Installing $spec failed (uv exited with $LASTEXITCODE)." -ForegroundColor Red
        return
    }

    # Make sure uv's tool folder (where sagex.exe lives) is on PATH for new terminals.
    & $uv tool update-shell | Out-Null

    Write-Host ""
    Write-Host "sagex is installed." -ForegroundColor Green
    if (-not (Get-Command sagex -ErrorAction SilentlyContinue)) {
        Write-Host "  Open a new terminal so the 'sagex' command is on your PATH."
    }
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  sagex auth login     # paste your API key"
    Write-Host "  sagex                # open the terminal app"
    Write-Host "  sagex --help         # every command"
}

Install-Sagex
Remove-Item Function:\Install-Sagex -ErrorAction SilentlyContinue
