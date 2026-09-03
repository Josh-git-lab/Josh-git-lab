"""Refresh the gh-ascii profile card from gh.crafter.run.

    python scripts/fetch_ascii_card.py

This is the one asset on the profile that is NOT generated inside this
repository, so it is the one asset that can break without warning. Everything
downloaded is therefore validated before it is allowed to overwrite the
committed file:

* the response must parse as XML with an ``<svg>`` root
* it must be large enough to be a real card rather than an error page
* it must contain no ``<script>``
* it must contain no external references - GitHub serves README images through
  its camo proxy, which blocks outbound requests, so a card that hotlinks its
  portrait would render empty on the profile

A failed check leaves the existing file untouched and exits non-zero, so the
scheduled workflow surfaces the problem instead of committing a broken card.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.dom import minidom

ROOT = Path(__file__).resolve().parent.parent

ENDPOINT = "https://gh.crafter.run/Josh-git-lab"
IMAGE = "https://i.pinimg.com/1200x/5b/9e/b7/5b9eb7bb72be79ced6865fee313221ec.jpg"
COLS = "80"
BACKGROUND = "keep"

THEMES = {"dark": "dark_mode.svg", "light": "light_mode.svg"}

MIN_BYTES = 2000


def url_for(theme: str) -> str:
    query = urllib.parse.urlencode(
        {"theme": theme, "cols": COLS, "image": IMAGE, "bg": BACKGROUND}
    )
    return f"{ENDPOINT}?{query}"


def download(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "self-generating-profile", "Accept": "image/svg+xml,*/*"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}")
        return response.read().decode("utf-8")


def validate(svg: str) -> None:
    if len(svg.encode("utf-8")) < MIN_BYTES:
        raise RuntimeError(f"response is only {len(svg)} chars; likely an error page")
    document = minidom.parseString(svg)  # raises on malformed XML
    if document.documentElement.tagName.lower() != "svg":
        raise RuntimeError(f"root element is <{document.documentElement.tagName}>, not <svg>")
    if "<script" in svg.lower():
        raise RuntimeError("card contains <script>")
    external = [
        url
        for url in re.findall(r'(?:href|xlink:href|src)\s*=\s*["\']([^"\']+)', svg)
        if url.startswith(("http://", "https://", "//"))
        and not url.startswith("http://www.w3.org/")
    ]
    if external:
        raise RuntimeError(f"card references external resources, which GitHub blocks: {external}")


def main() -> int:
    failures = 0
    for theme, filename in THEMES.items():
        target = ROOT / filename
        try:
            svg = download(url_for(theme))
            validate(svg)
        # Broad on purpose: a network error, an HTML error page and malformed
        # XML all mean the same thing here - keep what we already have.
        except Exception as exc:
            print(f"{filename:<16} FAILED: {exc}", file=sys.stderr)
            print(f"{'':<16} keeping the existing file", file=sys.stderr)
            failures += 1
            continue

        previous = target.read_text(encoding="utf-8") if target.exists() else None
        if previous == svg:
            print(f"{filename:<16} unchanged  {len(svg):>6} bytes")
            continue
        target.write_text(svg, encoding="utf-8", newline="\n")
        state = "updated" if previous is not None else "created"
        print(f"{filename:<16} {state:<9}  {len(svg):>6} bytes")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
