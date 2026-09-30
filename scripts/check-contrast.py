"""Fail if any theme text token is below WCAG AA (4.5:1) on any surface, in light or dark."""

import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "packages/ui/src/styles.css"
TEXT = ("foreground", "muted", "profit", "loss", "warning", "primary-text")
SURFACES = ("background", "surface", "surface-2")


def tokens(css: str, selector: str) -> dict[str, str]:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", css, re.S)
    if not m:
        raise SystemExit(f"{selector} not found in {CSS}")
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", m.group(1)))


def luminance(hex_colour: str) -> float:
    c = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def main() -> int:
    css = CSS.read_text()
    failures = []
    for theme, selector in (("light", ":root"), ("dark", ".dark")):
        t = tokens(css, selector)
        pairs = [(fg, bg) for fg in TEXT for bg in SURFACES] + [("primary-foreground", "primary")]
        for fg, bg in pairs:
            r = ratio(t[fg], t[bg])
            if r < 4.5:
                failures.append(f"{theme}: {fg} on {bg} = {r:.2f}:1")
    for f in failures:
        print("FAIL", f)
    print("contrast OK" if not failures else f"{len(failures)} contrast failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
