"""Regenerate the SageCode logo files in web/assets/img/.

The mark ("CLI Flow"): an S drawn as a pipeline in two strokes.
  - An upper hook starts at a node (top right), runs left, and curls round in one
    wide arc that wraps a second node, like Autosage's "@" wrapping its gear.
  - From that node a lower run heads right, turns down round a second wide arc,
    and comes back left to an end node.
  - A terminal prompt (>_) sits in the lower curve.
The gradient follows the flow: light where the pipeline starts, deeper at the end.

Geometry is measured from the reference artwork, in its own units (the mark spans
roughly x 46–154, y 2–96).

    python web/tools/build_logo.py

Writes:
  logo.svg, favicon.svg   black app-icon tile (favicon uses a heavier small-size cut)
  logo-light.svg          white tile
  mark-dark.svg           bare mark for dark backgrounds (site header/footer, dark theme)
  mark-light.svg          bare mark for light backgrounds (light theme)
  mark.svg                one-colour mark in currentColor
  apple-touch-icon.svg    full-bleed square → rasterise to apple-touch-icon.png (180×180)
"""

import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "assets" / "img"

# ── Geometry (reference units) ─────────────────────────────────────────────────
TOP_Y = 9.5                       # upper run
HOOK_C, HOOK_R = (76.0, 36.0), 26.5
HOOK_END_DEG = 111                # where the hook's tail stops (0° = right, y down)
TOP_NODE = (128.0, 9.5)
MID_NODE = (74.0, 33.5)           # sits at the hook's centre
LOW_C, LOW_R = (122.0, 61.25), 27.75
BOT_Y = LOW_C[1] + LOW_R          # 89
END_NODE = (60.0, BOT_Y)
NODE_R = 7.0
MID_NODE_R = 6.5
PORT_GAP = 2.2                    # the small gap between the top node and its run
BBOX = (46.0, 2.5, 154.0, 96.0)   # x0, y0, x1, y1 of the inked mark

# Optical sizes: the display cut matches the reference; the small cut (favicon,
# 16–32 px) is heavier and drops the prompt, which can't survive at that size.
DISPLAY = {"stroke": 7.5, "node": 1.0, "prompt": True}
SMALL = {"stroke": 14.0, "node": 1.35, "prompt": False}

# ── Palettes: (hook start, hook end), (low start, low mid, low end), prompt ────
PALETTES = {
    # Autosage purple on dark: lavender → nebula → royal.
    "dark": (("#E7C9FB", "#C68FF2"), ("#D9ABF7", "#A65BE6", "#7A2FD0"), "#ECE6F7"),
    # Deeper on light backgrounds.
    "light": (("#9B4FE0", "#7A1FB8"), ("#8A3FD6", "#6B12A8", "#3F0071"), "#0C0B12"),
}


def _p(x: float, y: float) -> str:
    return f"{x:.2f} {y:.2f}"


def _on(c, r, deg):
    a = math.radians(deg)
    return c[0] + r * math.cos(a), c[1] + r * math.sin(a)


def paths(stroke: float) -> tuple[str, str]:
    """(upper hook, lower run) as SVG path data."""
    hook_top = (HOOK_C[0], HOOK_C[1] - HOOK_R)                       # = (76, 9.5)
    start_x = TOP_NODE[0] - NODE_R - PORT_GAP - stroke / 2           # leave the port gap
    hook = (f"M{_p(start_x, TOP_Y)} H{hook_top[0]:.2f} "
            f"A{HOOK_R} {HOOK_R} 0 0 0 {_p(*_on(HOOK_C, HOOK_R, HOOK_END_DEG))}")
    low_top, low_bot = (LOW_C[0], LOW_C[1] - LOW_R), (LOW_C[0], LOW_C[1] + LOW_R)
    low_mid = _on(LOW_C, LOW_R, 0)                                   # rightmost point
    low = (f"M{_p(*MID_NODE)} H{low_top[0]:.2f} "
           f"A{LOW_R} {LOW_R} 0 0 1 {_p(*low_mid)} A{LOW_R} {LOW_R} 0 0 1 {_p(*low_bot)} "
           f"H{END_NODE[0]:.2f}")
    return hook, low


def glyph(palette: str | None, cut: dict, ids: str, mono: str = "currentColor") -> tuple[str, str]:
    """(defs, body). palette=None draws everything in `mono`."""
    hook, low = paths(cut["stroke"])
    nr, mr = NODE_R * cut["node"], MID_NODE_R * cut["node"]
    if palette:
        (h0, h1), (l0, l1, l2), ink = PALETTES[palette]
        defs = (f'<linearGradient id="{ids}h" x1="130" y1="8" x2="52" y2="58" gradientUnits="userSpaceOnUse">'
                f'<stop stop-color="{h0}"/><stop offset="1" stop-color="{h1}"/></linearGradient>'
                f'<linearGradient id="{ids}l" x1="70" y1="20" x2="140" y2="95" gradientUnits="userSpaceOnUse">'
                f'<stop stop-color="{l0}"/><stop offset=".45" stop-color="{l1}"/><stop offset="1" stop-color="{l2}"/></linearGradient>')
        hc, lc = f"url(#{ids}h)", f"url(#{ids}l)"
    else:
        defs, hc, lc, ink = "", mono, mono, mono
    sw = cut["stroke"]
    body = (f'<path d="{hook}" fill="none" stroke="{hc}" stroke-width="{sw}" stroke-linecap="round"/>'
            f'<path d="{low}" fill="none" stroke="{lc}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round"/>'
            f'<circle cx="{TOP_NODE[0]}" cy="{TOP_NODE[1]}" r="{nr}" fill="{hc}"/>'
            f'<circle cx="{MID_NODE[0]}" cy="{MID_NODE[1]}" r="{mr}" fill="{lc}"/>'
            f'<circle cx="{END_NODE[0]}" cy="{END_NODE[1]}" r="{nr}" fill="{lc}"/>')
    if cut["prompt"]:
        body += (f'<path d="M83 50 L95 59.5 L83 69" fill="none" stroke="{ink}" stroke-width="3.4" '
                 f'stroke-linecap="round" stroke-linejoin="round"/>'
                 f'<path d="M101 69 H117" stroke="{ink}" stroke-width="3.4" stroke-linecap="round"/>')
    return defs, body


def _view(pad: float) -> str:
    x0, y0, x1, y1 = BBOX
    return f"{x0 - pad:.1f} {y0 - pad:.1f} {x1 - x0 + 2 * pad:.1f} {y1 - y0 + 2 * pad:.1f}"


def bare(palette: str | None, ids: str) -> str:
    defs, body = glyph(palette, DISPLAY, ids)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{_view(2)}"><title>SageCode</title>'
            f'{f"<defs>{defs}</defs>" if defs else ""}{body}</svg>\n')


def tile(dark: bool = True, rounded: bool = True, small: bool = False) -> str:
    """The mark centred on a 64×64 tile, 74% of the tile wide."""
    cut = SMALL if small else DISPLAY
    defs, body = glyph("dark" if dark else "light", cut, "sct")
    x0, y0, x1, y1 = BBOX
    s = 64 * 0.74 / (x1 - x0)
    tx, ty = 32 - s * (x0 + x1) / 2, 32 - s * (y0 + y1) / 2
    bg, edge_c = ("#0A0A0F", "#ffffff") if dark else ("#ffffff", "#0C0B12")
    rx = 15 if rounded else 0
    edge = (f'<rect x=".75" y=".75" width="62.5" height="62.5" rx="14.25" fill="none" stroke="{edge_c}" '
            f'stroke-opacity=".1" stroke-width="1.5"/>' if rounded else "")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><title>SageCode</title>'
            f'<defs>{defs}</defs><rect width="64" height="64" rx="{rx}" fill="{bg}"/>{edge}'
            f'<g transform="translate({tx:.3f} {ty:.3f}) scale({s:.4f})">{body}</g></svg>\n')


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    files = {
        "logo.svg": tile(dark=True),
        "favicon.svg": tile(dark=True, small=True),
        "logo-light.svg": tile(dark=False),
        "mark-dark.svg": bare("dark", "scd"),
        "mark-light.svg": bare("light", "scl"),
        "mark.svg": bare(None, "scm"),
        "apple-touch-icon.svg": tile(dark=True, rounded=False),
    }
    for name, svg in files.items():
        (OUT / name).write_text(svg, encoding="utf-8")
    print("wrote " + ", ".join(files) + f" in {OUT}")


if __name__ == "__main__":
    main()
