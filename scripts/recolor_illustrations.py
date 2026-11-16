#!/usr/bin/env python3
"""scripts/recolor_illustrations.py — re-tint the hand-drawn workshop
illustrations from one theme's palette into another's.

The set was drawn for the Pinamar installation: black line art, a mint-green
accent fill, white paper. The public demo runs the `humanrounds` theme —
warm paper, vermilion, dark-umber ink — so the same drawings arrived on a
cream page carrying somebody else's green (the product owner, 2026-08-04: "for the
workshops, recolor the images to match the demo's color scheme").

Recoloring, not regenerating: these are the SAME drawings, and a fresh
generation pass would have produced eleven different ones for no reason.
The transform is a hue rotation in HSV — the accent's hue moves to the
target's, saturation and value are left alone, so the pastel stays pastel
and the pencil texture survives. Paper and ink are matched separately, by
lightness, because they are near-neutral and a hue rotation would not touch
them.

Deterministic: same input, same output, so re-running produces no diff.

Usage:
    python3 scripts/recolor_illustrations.py \\
        --src /path/to/private-town-pack/talleres \\
        --out packs/example-town/talleres --theme humanrounds
    python3 scripts/recolor_illustrations.py ... --preview   # 1 file only
"""
import argparse
import colorsys
import os
import sys

try:
    from PIL import Image
except ImportError:  # pragma: no cover - a dev without Pillow
    print("Pillow required: python3 -m pip install --break-system-packages Pillow")
    sys.exit(1)

# Target palettes, read off human-rounds.css. `accent` is the hue the drawn
# fill becomes; `paper`/`ink` replace the near-white and near-black ends.
THEMES = {
    "humanrounds": {
        "accent": "#c4603c",   # between --accent #bd2f16 and --av-hero-a #d3795a:
                               # the vermilion itself is a text/button colour and
                               # reads as an alarm when it fills half a drawing
        "paper": "#f7f3eb",    # --bg
        "ink": "#181716",      # --text
    },
    "pinamar": {
        "accent": "#17a46a",   # --av-hero-a
        "paper": "#ffffff",
        "ink": "#111111",
    },
}

# A pixel this unsaturated is paper or ink, never the accent fill.
_NEUTRAL_S = 0.12
# Above this lightness a neutral pixel is paper; below it, ink. Mid-greys are
# the pencil's anti-aliasing and get interpolated between the two.
_PAPER_V = 0.90
_INK_V = 0.35


def _rgb(hex_colour):
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def recolor(image, theme):
    """One image, re-tinted. Works on RGB; returns a new image."""
    accent_h, _accent_s, _accent_v = colorsys.rgb_to_hsv(
        *[c / 255 for c in _rgb(theme["accent"])])
    paper = _rgb(theme["paper"])
    ink = _rgb(theme["ink"])

    src = image.convert("RGB")
    out = Image.new("RGB", src.size)
    put = []
    for r, g, b in src.getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s >= _NEUTRAL_S:
            # the drawn accent: same pastel, different hue
            nr, ng, nb = colorsys.hsv_to_rgb(accent_h, s, v)
            put.append((round(nr * 255), round(ng * 255), round(nb * 255)))
        elif v >= _PAPER_V:
            put.append(paper)
        elif v <= _INK_V:
            put.append(ink)
        else:
            # the pencil's soft edge — walk from ink to paper the same way the
            # original did, so the line keeps its weight instead of hardening
            t = (v - _INK_V) / (_PAPER_V - _INK_V)
            put.append(_mix(ink, paper, t))
    out.putdata(put)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, help="Directory of source images.")
    parser.add_argument("--out", required=True, help="Directory to write into.")
    parser.add_argument("--theme", default="humanrounds", choices=sorted(THEMES))
    parser.add_argument("--preview", action="store_true",
                        help="Convert only the first image, for a look.")
    parser.add_argument("--quality", type=int, default=88)
    args = parser.parse_args()

    theme = THEMES[args.theme]
    os.makedirs(args.out, exist_ok=True)
    names = sorted(n for n in os.listdir(args.src)
                   if n.lower().endswith((".jpg", ".jpeg", ".png")))
    if args.preview:
        names = names[:1]

    for name in names:
        img = Image.open(os.path.join(args.src, name))
        dest = os.path.join(args.out, os.path.splitext(name)[0] + ".jpg")
        recolor(img, theme).save(dest, "JPEG", quality=args.quality, optimize=True)
        print(f"  {name} -> {dest}")
    print(f"{len(names)} image(s), theme '{args.theme}'.")


if __name__ == "__main__":
    main()
