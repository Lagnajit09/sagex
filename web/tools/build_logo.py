"""Regenerate the sagex logo files in web/assets/img/.

The mark, on a 64×64 grid:
  - a hexagon: the infrastructure cell (a cluster, a node, a container), drawn faint;
  - a bold "S" traced through it: the automation, and the name;
  - a node at each end of the S: the agents / servers it connects;
  - a four-point spark at the core: the AI that orchestrates it.
It is 180°-symmetric, and reads as an S down to favicon size.

    python web/tools/build_logo.py

Writes logo.svg + favicon.svg (brand tile), mark.svg (bare glyph in currentColor,
for monochrome use on any background) and apple-touch-icon.svg, which is
rasterised to apple-touch-icon.png (180×180) by any browser or SVG renderer.
"""

import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "assets" / "img"

C = 32                    # centre
HEX_R = 22.5              # hexagon circumradius (pointy-top)
S_STROKE = 5.8            # the S
EDGE_STROKE = 2.2         # the faint hexagon edges
EDGE_OPACITY = 0.42
CORNER_R = 4.5            # rounding at the S's bends
NODE_R = 4.4              # agent nodes at the S's ends
SPARK_R, SPARK_WAIST = 7.6, 2.35
SPARK_GAP = 7.2           # clear space cut round the spark (an SVG mask, so any background works)


def _p(x: float, y: float) -> str:
    return f"{x:.2f} {y:.2f}"


def _hex() -> list[tuple[float, float]]:
    """Vertices T, UR, LR, B, LL, UL."""
    return [(C + HEX_R * math.cos(math.radians(a)), C + HEX_R * math.sin(math.radians(a)))
            for a in (-90, -30, 30, 90, 150, 210)]


def _rounded(points: list[tuple[float, float]], r: float) -> str:
    """A polyline whose interior corners are rounded (quadratic) over distance r."""
    def toward(a, b, dist):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        return a[0] + (b[0] - a[0]) * dist / length, a[1] + (b[1] - a[1]) * dist / length

    d = f"M{_p(*points[0])}"
    for prev, corner, nxt in zip(points, points[1:], points[2:]):
        d += f" L{_p(*toward(corner, prev, r))} Q{_p(*corner)} {_p(*toward(corner, nxt, r))}"
    return d + f" L{_p(*points[-1])}"


def _spark() -> str:
    pts = []
    for i in range(8):
        a = math.radians(i * 45 - 90)
        r = SPARK_R if i % 2 == 0 else SPARK_WAIST
        pts.append((C + r * math.cos(a), C + r * math.sin(a)))
    d = f"M{_p(*pts[0])}" + "".join(f" Q{_p(*pts[i + 1])} {_p(*pts[(i + 2) % 8])}" for i in range(0, 8, 2))
    return d + " Z"


def glyph(fill: str, mask_id: str) -> tuple[str, str]:
    """(defs, body) for the mark in `fill`."""
    t, ur, lr, b, ll, ul = _hex()
    outline = "M" + " L".join(_p(*v) for v in (t, ur, lr, b, ll, ul)) + " Z"
    s_path = _rounded([ur, t, ul, (C, C), lr, b, ll], CORNER_R)
    defs = (f'<mask id="{mask_id}" maskUnits="userSpaceOnUse" x="0" y="0" width="64" height="64">'
            f'<rect width="64" height="64" fill="#fff"/><circle cx="{C}" cy="{C}" r="{SPARK_GAP}" fill="#000"/></mask>')
    body = (f'<g mask="url(#{mask_id})" fill="none" stroke="{fill}" stroke-linecap="round" stroke-linejoin="round">'
            f'<path d="{outline}" stroke-width="{EDGE_STROKE}" stroke-opacity="{EDGE_OPACITY}"/>'
            f'<path d="{s_path}" stroke-width="{S_STROKE}"/></g>'
            f'<circle cx="{ur[0]:.2f}" cy="{ur[1]:.2f}" r="{NODE_R}" fill="{fill}"/>'
            f'<circle cx="{ll[0]:.2f}" cy="{ll[1]:.2f}" r="{NODE_R}" fill="{fill}"/>'
            f'<path d="{_spark()}" fill="{fill}"/>')
    return defs, body


GRADIENT = ('<linearGradient id="sagex-tile" x1="6" y1="4" x2="58" y2="62" gradientUnits="userSpaceOnUse">'
            '<stop stop-color="#3F0071"/><stop offset=".55" stop-color="#610094"/><stop offset="1" stop-color="#B373E7"/>'
            '</linearGradient>')


def tile(rounded: bool = True) -> str:
    defs, body = glyph("#fff", "sagex-cut")
    rx = 15 if rounded else 0
    edge = ('<rect x=".75" y=".75" width="62.5" height="62.5" rx="14.25" fill="none" stroke="#fff" '
            'stroke-opacity=".16" stroke-width="1.5"/>' if rounded else "")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><title>sagex</title>'
            f'<defs>{GRADIENT}{defs}</defs><rect width="64" height="64" rx="{rx}" fill="url(#sagex-tile)"/>'
            f'{edge}{body}</svg>\n')


def mark() -> str:
    defs, body = glyph("currentColor", "sagex-mark-cut")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="7 6 50 52"><title>sagex</title>'
            f'<defs>{defs}</defs>{body}</svg>\n')


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "logo.svg").write_text(tile(), encoding="utf-8")
    (OUT / "favicon.svg").write_text(tile(), encoding="utf-8")
    (OUT / "mark.svg").write_text(mark(), encoding="utf-8")
    # iOS masks its own rounded corners, so the touch icon is a full-bleed square.
    (OUT / "apple-touch-icon.svg").write_text(tile(rounded=False), encoding="utf-8")
    print(f"wrote logo.svg, favicon.svg, mark.svg, apple-touch-icon.svg in {OUT}")


if __name__ == "__main__":
    main()
