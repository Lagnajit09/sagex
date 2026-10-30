# SageCode website

The static site at **https://sagecode.web.app**: plain HTML, CSS and JavaScript,
with no build step and no dependencies.

The product is presented as **SageCode**; the command it installs is `sagex`
(PyPI package `sagex-cli`). Keep `sagex` wherever text means the command itself.

```
web/
├── index.html, install.html, guide.html, commands.html, changelog.html, 404.html
├── assets/
│   ├── css/site.css            # the whole design system (dark + light themes)
│   ├── js/site.js              # theme, install tabs, copy buttons, hero animation
│   ├── js/commands.js          # renders the command reference
│   ├── js/commands-data.js     # GENERATED from the CLI
│   └── img/                    # logo + marks, favicon (GENERATED), TUI screenshot (GENERATED), og-cover
└── tools/                      # regeneration scripts (not deployed)
```

## Preview locally

```sh
python -m http.server 8000 --directory web
```

Then open http://localhost:8000. Add `?theme=light` or `?theme=dark` to a URL to
force a theme.

## Keep it in sync with the CLI

Run these from the repo root, with sagex installed (`pip install -e .`):

| When | Run |
|---|---|
| A command or option changed | `python web/tools/build_commands.py` |
| The terminal app's look changed | `python web/tools/tui_screenshot.py` |
| The header or footer changed (edit it in `index.html`) | `python web/tools/sync_chrome.py` |
| The logo changed | `python web/tools/build_logo.py`, then re-render `apple-touch-icon.png` (the header uses `mark-dark.svg` / `mark-light.svg`, one per theme) |
| The share image changed | re-render `tools/og-cover.html` (see the comment at its top) |

The landing page's command count (56) and the version in the header and footer
are written by hand; update them when they change.

## Deploy

Pushing to `main` deploys automatically (`.github/workflows/website.yml`), and
pull requests get a preview URL. The workflow copies `scripts/install.sh` and
`scripts/install.ps1` into the site, so `https://sagecode.web.app/install.sh`
always matches the repo.

It needs the repo secret `FIREBASE_SERVICE_ACCOUNT`. To deploy by hand instead:

```sh
cp scripts/install.sh scripts/install.ps1 web/
firebase deploy --only hosting:site
```
