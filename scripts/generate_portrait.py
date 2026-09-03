"""Turn a photograph into an animated ASCII portrait SVG.

    python scripts/generate_portrait.py

Reads assets/portrait.jpg and writes generated/portrait.svg. The pipeline is:

    square crop (face-aware when OpenCV is installed)
      -> grayscale
      -> local contrast equalisation, so uneven lighting does not wash out
         one side of the face
      -> percentile stretch onto the full tonal range
      -> darkening curve, which thins out the background and leaves the
         features as the densest glyphs
      -> area-average downsample onto the character grid
      -> brightness mapped through the ASCII ramp

The result is emitted twice, once per colour polarity. Glyph density has to
track luminance on a dark panel and track darkness on a light panel, otherwise
the portrait renders as a photographic negative in one of GitHub's two themes.
A ``prefers-color-scheme`` rule shows exactly one of the two.

The reveal is SMIL only - no JavaScript, which GitHub would strip anyway. Each
row is clipped by a rect whose width animates from zero once, with
``fill="freeze"`` so the finished portrait stays on screen.
"""

from __future__ import annotations

import argparse
import base64
import math
import sys
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
CANDIDATES = ("portrait.jpg", "portrait.jpeg", "portrait.png", "portrait.webp")

# Sparse to dense. The leading space lets the background drop out completely.
RAMP = " .`:-=+*cs#%@"

COLS = 92
FONT_SIZE = 8.0
ADVANCE = 0.6                       # JetBrains Mono glyph advance, in em
CHAR_W = FONT_SIZE * ADVANCE        # 4.8px
CHAR_H = CHAR_W * 2.0               # 9.6px, so a square crop stays square
PADDING = 26.0

ROW_STEP = 0.045                    # stagger between row reveals
ROW_DUR = 0.34                      # how long one row takes to sweep in

THEMES = {
    # id: (background, border, ink, label, accent, density tracks luminance?)
    "light": ("#fbfbf9", "#e4e3de", "#15171a", "#8f959c", "#a8562b", False),
    "dark": ("#0e0f12", "#23252b", "#e9ebee", "#6b7280", "#e0885f", True),
}


# --------------------------------------------------------------------------- #
# Image pipeline
# --------------------------------------------------------------------------- #

def find_source(explicit: str | None) -> Path | None:
    if explicit:
        path = Path(explicit)
        return path if path.exists() else None
    for name in CANDIDATES:
        path = ROOT / "assets" / name
        if path.exists():
            return path
    return None


def face_square(image) -> tuple[int, int, int] | None:
    """Locate a head-and-shoulders square using OpenCV, if it is available.

    OpenCV is optional; without it we fall back to a centred crop biased
    upwards, which is where a face sits in almost every portrait photo.
    """
    try:
        import cv2
        import numpy as np
    except ModuleNotFoundError:
        return None

    gray = np.asarray(image.convert("L"))
    cascade = cv2.CascadeClassifier(
        str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
    )
    faces = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=6, minSize=(48, 48))
    if len(faces) == 0:
        return None

    x, y, w, h = max(faces, key=lambda f: int(f[2]) * int(f[3]))
    side = int(min(min(gray.shape), h * 2.6))
    # Push the crop centre below the face centre so the frame keeps some
    # shoulder rather than cutting off at the chin.
    cx = x + w / 2
    cy = y + h / 2 + side * 0.10
    left = int(round(min(max(cx - side / 2, 0), gray.shape[1] - side)))
    top = int(round(min(max(cy - side / 2, 0), gray.shape[0] - side)))
    return left, top, side


def crop_square(image):
    box = face_square(image)
    if box is None:
        width, height = image.size
        side = min(width, height)
        left = (width - side) // 2
        top = int((height - side) * 0.34)  # bias upwards toward the head
        box = (left, top, side)
    left, top, side = box
    return image.crop((left, top, left + side, top + side))


def to_grid(path: Path, cols: int, gamma: float) -> list[str]:
    """Reduce the photograph to ``cols`` wide grid of ramp indices per row."""
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps

    rows = int(round(cols * CHAR_W / CHAR_H))

    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    image = crop_square(image)
    # Work at a fixed multiple of the grid: large enough for the contrast
    # filters to have something to chew on, small enough to stay fast, and
    # independent of the source resolution so output is reproducible.
    work = 8 * cols
    image = image.resize((work, work), Image.Resampling.LANCZOS).convert("L")

    try:
        import cv2
        pixels = cv2.createCLAHE(clipLimit=2.4, tileGridSize=(8, 8)).apply(np.asarray(image))
        flat = np.asarray(pixels, dtype=np.float32)
    except ModuleNotFoundError:
        # High-pass the image against a heavy blur to cancel uneven lighting,
        # then blend it back so the portrait keeps its overall tonal shape.
        base = np.asarray(image, dtype=np.float32)
        blur = np.asarray(image.filter(ImageFilter.GaussianBlur(work / 12)), dtype=np.float32)
        flat = np.clip(0.45 * base + 0.55 * (base - blur + base.mean()), 0, 255)

    # Percentile stretch: ignore the extreme 1% at each end so a single blown
    # highlight cannot compress everything else into the middle of the ramp.
    low, high = np.percentile(flat, (1.0, 99.0))
    if high - low < 1e-6:
        raise SystemExit(f"{path} has no tonal range to work with")
    flat = np.clip((flat - low) / (high - low), 0.0, 1.0)

    sharp = Image.fromarray((flat * 255).astype(np.uint8)).filter(
        ImageFilter.UnsharpMask(radius=max(1, work // 220), percent=110, threshold=3)
    )
    small = np.asarray(
        sharp.resize((cols, rows), Image.Resampling.BOX), dtype=np.float32
    ) / 255.0

    # Darkening curve. gamma > 1 pulls the midtones down, which empties the
    # background and keeps eyes, nose and mouth as the densest glyphs.
    small = np.power(small, gamma)
    small = (small - small.min()) / max(float(np.ptp(small)), 1e-6)

    levels = np.clip(np.rint(small * (len(RAMP) - 1)).astype(int), 0, len(RAMP) - 1)
    return ["".join(RAMP[i] for i in row) for row in levels]


# --------------------------------------------------------------------------- #
# SVG rendering
# --------------------------------------------------------------------------- #

def font_faces() -> str:
    path = ROOT / "fonts" / "subset" / "regular.woff2"
    if not path.exists():
        return ""
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return (
        "@font-face{font-family:'JBMono';font-style:normal;font-weight:400;"
        f"font-display:block;src:url(data:font/woff2;base64,{data}) format('woff2')}}"
    )


def render(grid: list[str], *, name: str) -> str:
    rows, cols = len(grid), len(grid[0])
    grid_w = cols * CHAR_W
    grid_h = rows * CHAR_H
    # Whole-pixel canvas, with the character grid centred in whatever padding
    # rounding leaves behind.
    width = math.ceil(grid_w + 2 * PADDING)
    height = math.ceil(grid_h + 2 * PADDING + 22)
    grid_x = round((width - grid_w) / 2, 2)
    grid_y = round(height - PADDING - grid_h, 2)
    duration = ROW_STEP * (rows - 1) + ROW_DUR

    # One clip per row, shared by both colour polarities.
    clips = []
    for i in range(rows):
        begin = round(i * ROW_STEP, 3)
        clips.append(
            f'<clipPath id="r{i}">'
            f'<rect x="{_n(grid_x)}" y="{_n(grid_y + i * CHAR_H - 1)}" '
            f'width="0" height="{_n(CHAR_H + 2)}">'
            f'<animate attributeName="width" from="0" to="{_n(grid_w)}" '
            f'begin="{begin}s" dur="{ROW_DUR}s" fill="freeze"/>'
            f"</rect></clipPath>"
        )

    style = (
        font_faces()
        + "text{font-variant-ligatures:none;white-space:pre}"
        + "#p-dark{display:none}"
        + "@media (prefers-color-scheme:dark){#p-light{display:none}#p-dark{display:inline}}"
    )

    panels = []
    for theme, (bg, border, ink, label, accent, bright_is_dense) in THEMES.items():
        rendered = grid if bright_is_dense else [_invert(row) for row in grid]
        parts = [
            f'<rect x="0.5" y="0.5" width="{_n(width - 1)}" height="{_n(height - 1)}" rx="10" '
            f'fill="{bg}" stroke="{border}" stroke-width="1"/>',
            f'<text x="{_n(grid_x)}" y="{_n(grid_y - 18)}" fill="{label}" font-size="9" '
            f'letter-spacing="1.6">ASCII PORTRAIT</text>',
            f'<text x="{_n(width - grid_x)}" y="{_n(grid_y - 18)}" fill="{label}" font-size="9" '
            f'letter-spacing="1.1" text-anchor="end">{cols}\u00d7{rows} \u00b7 SELF-GENERATED</text>',
        ]
        for i, row in enumerate(rendered):
            baseline = grid_y + i * CHAR_H + CHAR_H * 0.78
            parts.append(
                f'<text clip-path="url(#r{i})" x="{_n(grid_x)}" y="{_n(baseline)}" '
                f'fill="{ink}" font-size="{_n(FONT_SIZE)}" textLength="{_n(grid_w)}" '
                f'lengthAdjust="spacing" xml:space="preserve">{escape(row)}</text>'
            )
        # A single scan line tracks the reveal and fades out for good.
        parts.append(
            f'<rect x="{_n(grid_x)}" y="{_n(grid_y)}" width="{_n(grid_w)}" height="1" '
            f'fill="{accent}" opacity="0">'
            f'<animate attributeName="y" from="{_n(grid_y)}" to="{_n(grid_y + grid_h)}" '
            f'begin="0s" dur="{_n(duration)}s" fill="freeze"/>'
            f'<animate attributeName="opacity" values="0;0.55;0.55;0" keyTimes="0;0.05;0.88;1" '
            f'begin="0s" dur="{_n(duration)}s" fill="freeze"/>'
            f"</rect>"
        )
        panels.append(f'<g id="p-{theme}"' + (' display="none"' if theme == "dark" else "") + ">"
                      + "".join(parts) + "</g>")

    title = f"Animated ASCII portrait of {name}, generated from a photograph in this repository"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(width)}" height="{_n(height)}" '
        f'viewBox="0 0 {_n(width)} {_n(height)}" role="img" aria-label="{title}">'
        f"<title>{escape(title)}</title>"
        f'<defs><style type="text/css">/*<![CDATA[*/{style}/*]]>*/</style>{"".join(clips)}</defs>'
        "<g font-family=\"'JBMono', ui-monospace, 'SFMono-Regular', Menlo, Consolas, monospace\">"
        f"{''.join(panels)}</g>"
        "</svg>\n"
    )


def _invert(row: str) -> str:
    last = len(RAMP) - 1
    return "".join(RAMP[last - RAMP.index(c)] for c in row)


def _n(value: float) -> str:
    rounded = round(float(value), 2)
    return str(int(rounded)) if rounded == int(rounded) else f"{rounded:g}"


# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", help="source photograph (default: assets/portrait.jpg)")
    parser.add_argument("--output", default=str(ROOT / "generated" / "portrait.svg"))
    parser.add_argument("--cols", type=int, default=COLS, help="character grid width")
    parser.add_argument("--gamma", type=float, default=1.25, help="darkening curve, >1 darkens")
    parser.add_argument("--name", default="Josh Jiby", help="name used in the SVG alt text")
    args = parser.parse_args(argv)

    source = find_source(args.input)
    if source is None:
        print(
            "No source photograph found.\n\n"
            "  Add a portrait image at assets/portrait.jpg and re-run:\n"
            "      python scripts/generate_portrait.py\n\n"
            "  A square-ish, well-lit, head-and-shoulders photo works best.\n"
            "  generated/portrait.svg was NOT written - nothing is invented here.",
            file=sys.stderr,
        )
        return 2

    grid = to_grid(source, max(24, args.cols), args.gamma)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(grid, name=args.name), encoding="utf-8", newline="\n")
    print(f"read    {source}")
    print(f"wrote   {output}  {output.stat().st_size} bytes  {len(grid[0])}\u00d7{len(grid)} characters")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
