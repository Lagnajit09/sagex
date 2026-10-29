"""Copy the shared header and footer from index.html into every other page.

index.html is the single source of truth for the site chrome. Each page marks
where it goes with <!-- chrome:header --> … <!-- /chrome:header --> (and the same
for footer); this rewrites those blocks, pointing in-page links back at
index.html and marking the current page's nav link with aria-current. Pages
stay plain, hand-editable HTML.

    python web/tools/sync_chrome.py
"""

import re
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parents[1]
BLOCK = r"(  <!-- chrome:{0} -->\n)(.*?)(  <!-- /chrome:{0} -->\n)"
STANDALONE = {"404.html"}     # served at any depth, so it has its own root-relative chrome


def block(html: str, name: str) -> str:
    m = re.search(BLOCK.format(name), html, flags=re.S)
    if not m:
        raise SystemExit(f"index.html has no chrome:{name} block")
    return m.group(2)


def for_page(chrome: str, page: str, mark_current: bool) -> str:
    chrome = chrome.replace(' aria-current="page"', "")
    chrome = re.sub(r'href="#', 'href="index.html#', chrome)            # in-page anchors → home page
    if not mark_current:                                                # only the main nav marks the page
        return chrome
    return chrome.replace(f'<a href="{page}">', f'<a href="{page}" aria-current="page">', 1)


def main() -> None:
    source = (WEB / "index.html").read_text(encoding="utf-8")
    header, footer = block(source, "header"), block(source, "footer")
    changed = 0
    for path in sorted(WEB.glob("*.html")):
        if path.name == "index.html" or path.name in STANDALONE:
            continue
        html = path.read_text(encoding="utf-8")
        new = html
        for name, chrome in (("header", header), ("footer", footer)):
            if not re.search(BLOCK.format(name), new, flags=re.S):
                print(f"skip {path.name}: no chrome:{name} block", file=sys.stderr)
                continue
            new = re.sub(BLOCK.format(name), lambda m: m.group(1) + for_page(chrome, path.name, name == "header") + m.group(3),
                         new, count=1, flags=re.S)
        if new != html:
            path.write_text(new, encoding="utf-8")
            changed += 1
            print(f"synced {path.name}")
    print(f"{changed} page(s) updated")


if __name__ == "__main__":
    main()
