"""Shared design system for every SVG this repository generates.

Three constraints shape the code here, all of them consequences of the fact
that GitHub renders these files inside an ``<img>`` tag:

1.  Nothing may be fetched at render time, so the JetBrains Mono subsets are
    base64-inlined as ``@font-face`` rules.
2.  A viewer that ignores the stylesheet must still get a readable asset, so
    every colour is written twice - once as a presentation attribute holding
    the light value, once as a CSS class. Presentation attributes lose to CSS,
    so the class wins when the stylesheet is honoured and the attribute is the
    fallback when it is not.
3.  Text is positioned with an explicit ``textLength`` wherever it has to line
    up with a grid, so a fallback font cannot break the layout.
"""

from __future__ import annotations

import base64
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
FONT_DIR = ROOT / "fonts" / "subset"
OUT_DIR = ROOT / "generated"

# JetBrains Mono advances every glyph by 600/1000 em.
ADVANCE = 0.6

FAMILY = "JBMono"
FONT_STACK = f"'{FAMILY}', ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace"

WIDTH = 840
PAD = 32

LIGHT = {
    "bg": "#fbfbf9",
    "border": "#e4e3de",
    "rule": "#ecece8",
    "ink": "#15171a",
    "ink2": "#575c63",
    "ink3": "#8f959c",
    "accent": "#a8562b",
    "cells": ["#eaeae5", "#e7cec0", "#d5a285", "#bf7a52", "#9d5227"],
    # Single-hue ramp that fades to neutral grey, not to the page background:
    # the smallest slices still have to be visible against the card. One step
    # per language slot, so no two entries share a swatch.
    "ramp": ["#8f4620", "#a1552b", "#b16639", "#bf7a4d", "#c68f66",
             "#c5a181", "#bda894", "#ada79f", "#9c9c9d", "#8b8f94"],
}

DARK = {
    "bg": "#0e0f12",
    "border": "#23252b",
    "rule": "#1c1e23",
    "ink": "#e9ebee",
    "ink2": "#a1a7b0",
    "ink3": "#6b7280",
    "accent": "#e0885f",
    "cells": ["#191b1f", "#3b2a21", "#6d4630", "#a86440", "#e0885f"],
    "ramp": ["#e0885f", "#d17f57", "#c0744f", "#ad6a49", "#985f43",
             "#82563e", "#6c4d3a", "#585043", "#4e5250", "#48505a"],
}

# Token -> (light value, dark value) for the flat colours.
_FLAT = [k for k in LIGHT if not isinstance(LIGHT[k], list)]


def _font_faces() -> str:
    """Inline every available subset as a base64 ``@font-face`` rule."""
    rules = []
    for name, weight in (("regular", 400), ("bold", 700)):
        path = FONT_DIR / f"{name}.woff2"
        if not path.exists():
            # Missing subsets degrade to the generic monospace stack rather
            # than failing the build; run scripts/build_font_subset.py to fix.
            continue
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        rules.append(
            f"@font-face{{font-family:'{FAMILY}';font-style:normal;font-weight:{weight};"
            f"font-display:block;src:url(data:font/woff2;base64,{data}) format('woff2')}}"
        )
    return "".join(rules)


def stylesheet(extra: str = "") -> str:
    """Build the ``<style>`` block: fonts, colour classes, dark-mode overrides."""
    light = "".join(f".f-{t}{{fill:{LIGHT[t]}}}.k-{t}{{stroke:{LIGHT[t]}}}" for t in _FLAT)
    dark = "".join(f".f-{t}{{fill:{DARK[t]}}}.k-{t}{{stroke:{DARK[t]}}}" for t in _FLAT)

    for i, (lo, hi) in enumerate(zip(LIGHT["cells"], DARK["cells"])):
        light += f".f-c{i}{{fill:{lo}}}"
        dark += f".f-c{i}{{fill:{hi}}}"
    for i, (lo, hi) in enumerate(zip(LIGHT["ramp"], DARK["ramp"])):
        light += f".f-r{i}{{fill:{lo}}}"
        dark += f".f-r{i}{{fill:{hi}}}"

    body = (
        _font_faces()
        + "text{font-variant-ligatures:none;white-space:pre}"
        + light
        + extra
        + f"@media (prefers-color-scheme:dark){{{dark}}}"
    )
    return f"<defs><style type=\"text/css\">/*<![CDATA[*/{body}/*]]>*/</style></defs>"


def token(name: str) -> tuple[str, str]:
    """Return the ``(class, light value)`` pair for a colour token."""
    if name.startswith("c") and name[1:].isdigit():
        return f"c{name[1:]}", LIGHT["cells"][int(name[1:])]
    if name.startswith("r") and name[1:].isdigit():
        return f"r{name[1:]}", LIGHT["ramp"][int(name[1:])]
    return name, LIGHT[name]


def paint(*, fill_token: str | None = None, stroke_token: str | None = None) -> str:
    """Emit fill and/or stroke as one merged ``class`` plus literal fallbacks.

    Both paints have to share a single ``class`` attribute - repeating it would
    make the document malformed XML.
    """
    classes, attrs = [], []
    if fill_token:
        cls, value = token(fill_token)
        classes.append(f"f-{cls}")
        attrs.append(f'fill="{value}"')
    if stroke_token:
        cls, value = token(stroke_token)
        classes.append(f"k-{cls}")
        attrs.append(f'stroke="{value}"')
    return f'class="{" ".join(classes)}" {" ".join(attrs)}'


def fill(name: str) -> str:
    return paint(fill_token=name)


def stroke(name: str) -> str:
    return paint(stroke_token=name)


def text(
    x: float,
    y: float,
    content: str,
    *,
    size: float = 11,
    color: str = "ink",
    weight: int = 400,
    anchor: str = "start",
    tracking: float | None = None,
    opacity: float | None = None,
    fixed_width: bool = False,
) -> str:
    """A single line of monospace text.

    ``fixed_width`` pins the run to its natural JetBrains Mono width via
    ``textLength``, which keeps character grids aligned even if the embedded
    font fails to load and a fallback monospace font is substituted.
    """
    attrs = [f'x="{_n(x)}"', f'y="{_n(y)}"', fill(color), f'font-size="{_n(size)}"']
    if weight != 400:
        attrs.append(f'font-weight="{weight}"')
    if anchor != "start":
        attrs.append(f'text-anchor="{anchor}"')
    if tracking:
        attrs.append(f'letter-spacing="{_n(tracking)}"')
    if opacity is not None:
        attrs.append(f'opacity="{_n(opacity)}"')
    if fixed_width:
        attrs.append(f'textLength="{_n(len(content) * size * ADVANCE)}" lengthAdjust="spacing"')
        attrs.append('xml:space="preserve"')
    return f"<text {' '.join(attrs)}>{escape(content)}</text>"


def rule(x1: float, y: float, x2: float, color: str = "rule") -> str:
    """A one-pixel horizontal hairline."""
    return f'<line x1="{_n(x1)}" y1="{_n(y)}" x2="{_n(x2)}" y2="{_n(y)}" {stroke(color)} stroke-width="1"/>'


def vrule(x: float, y1: float, y2: float, color: str = "rule") -> str:
    return f'<line x1="{_n(x)}" y1="{_n(y1)}" x2="{_n(x)}" y2="{_n(y2)}" {stroke(color)} stroke-width="1"/>'


def rect(x: float, y: float, w: float, h: float, *, color: str, radius: float = 0, opacity: float | None = None) -> str:
    attrs = [f'x="{_n(x)}"', f'y="{_n(y)}"', f'width="{_n(w)}"', f'height="{_n(h)}"', fill(color)]
    if radius:
        attrs.append(f'rx="{_n(radius)}"')
    if opacity is not None:
        attrs.append(f'opacity="{_n(opacity)}"')
    return f"<rect {' '.join(attrs)}/>"


def card(width: float, height: float) -> str:
    """The panel background shared by every statistics asset."""
    return (
        f'<rect x="0.5" y="0.5" width="{_n(width - 1)}" height="{_n(height - 1)}" rx="10" '
        f'{paint(fill_token="bg", stroke_token="border")} stroke-width="1"/>'
    )


def eyebrow(text_left: str, text_right: str = "", *, width: float = WIDTH, y: float = 34) -> str:
    """The small tracked-out label pair that heads every panel."""
    out = [text(PAD, y, text_left, size=9.5, color="ink3", tracking=1.6)]
    if text_right:
        out.append(text(width - PAD, y, text_right, size=9.5, color="ink3", tracking=1.1, anchor="end"))
    return "".join(out)


def document(width: float, height: float, body: str, *, title: str, extra_css: str = "") -> str:
    """Wrap panel content in a complete, standalone SVG document."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(width)}" height="{_n(height)}" '
        f'viewBox="0 0 {_n(width)} {_n(height)}" role="img" aria-label="{escape(title, {chr(34): "&quot;"})}">'
        f"<title>{escape(title)}</title>"
        f"{stylesheet(extra_css)}"
        f'<g font-family="{FONT_STACK}" font-size="11">{body}</g>'
        "</svg>\n"
    )


def write(name: str, content: str) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / name
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def _n(value: float) -> str:
    """Trim float noise so identical data always produces identical bytes."""
    if isinstance(value, int):
        return str(value)
    rounded = round(float(value), 2)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}"


def group(count: int, total: float, ratio: float = 0.62) -> tuple[float, float]:
    """Split ``total`` into ``count`` slots, returning ``(pitch, bar width)``."""
    pitch = total / count
    return pitch, max(1.0, round(pitch * ratio, 2))
