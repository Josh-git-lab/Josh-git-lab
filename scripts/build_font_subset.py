"""Build the tiny WOFF2 font subsets that every generated SVG embeds inline.

This is a one-time build tool, not part of the daily refresh. The resulting
files in ``fonts/subset/`` are committed so that neither GitHub Actions nor a
browser rendering the SVG ever has to fetch a font from the network.

    python -m pip install "fonttools[woff]" brotli
    python scripts/build_font_subset.py

Re-run it only if the character sets below change.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "fonts"
OUT = SRC / "subset"

# Printable ASCII covers every label, numeral and ASCII-ramp glyph we draw.
# U+00B7 is the technology-list separator, U+2192 the date-range arrow.
ASCII = "".join(chr(c) for c in range(0x20, 0x7F))
CAPS = "".join(chr(c) for c in range(0x41, 0x5B))
DIGITS = "0123456789"

FACES = {
    # name: (source file, character set)
    "regular": ("JetBrainsMonoNL-Regular.ttf", ASCII + "\u00b7\u2192"),
    # Bold is only ever used for headline numerals and uppercase labels, so it
    # can be subset far more aggressively than the regular weight.
    "bold": ("JetBrainsMonoNL-Bold.ttf", CAPS + DIGITS + " .,:;%/+-\u00b7\u2192"),
}


def build(src: Path, dst: Path, chars: str) -> None:
    from fontTools.subset import Options, Subsetter
    from fontTools.ttLib import TTFont

    options = Options()
    options.flavor = "woff2"
    options.with_zopfli = False
    options.desubroutinize = True
    options.hinting = False
    options.legacy_kern = False
    # Drop all OpenType layout: no kerning or contextual substitution can then
    # disturb the fixed advance width the ASCII grid relies on.
    options.layout_features = []
    options.drop_tables += ["GSUB", "GPOS", "GDEF", "BASE", "JSTF", "MATH"]
    options.name_IDs = ["*"]
    options.name_legacy = True
    options.notdef_outline = False
    options.recalc_bounds = True

    font = TTFont(src)
    subsetter = Subsetter(options=options)
    subsetter.populate(unicodes=sorted({ord(c) for c in chars}))
    subsetter.subset(font)
    font.flavor = "woff2"
    dst.parent.mkdir(parents=True, exist_ok=True)
    font.save(dst)
    font.close()


def main() -> int:
    try:
        import fontTools  # noqa: F401
    except ModuleNotFoundError:
        print('fontTools is required: python -m pip install "fonttools[woff]" brotli', file=sys.stderr)
        return 1

    for name, (filename, chars) in FACES.items():
        src = SRC / filename
        if not src.exists():
            print(f"missing source font: {src}", file=sys.stderr)
            return 1
        dst = OUT / f"{name}.woff2"
        build(src, dst, chars)
        print(f"{dst.relative_to(ROOT).as_posix():<28} {dst.stat().st_size:>6} bytes  {len(set(chars))} glyphs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
