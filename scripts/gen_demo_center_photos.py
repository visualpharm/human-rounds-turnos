#!/usr/bin/env python3
"""scripts/gen_demo_center_photos.py — the demo's health-center photos.

The Pinamar installation shows a real photo of each center; the public demo
is a fictional town, so there is nothing to photograph. Without images its
"Centros de salud" section was five text cards where production shows five
photos — the demo looked less like the product than the product does.

So the five buildings are generated (gpt-image-2) and written into
`packs/example-town/centro-photos/`, with `centros-info.json` pointing at
them the same way Pinamar's does. Deliberately generic municipal
architecture with NO legible signage: a fictional town must not appear to
name, or picture, a real place. `_note` in the generated JSON records that
these are synthetic, so nobody later mistakes them for photographs.

Idempotent: a center whose image already exists is skipped unless --force.

Usage:
    python3 scripts/gen_demo_center_photos.py
    python3 scripts/gen_demo_center_photos.py --only riverside-clinic --force

Set `OPENAI_API_KEY` directly, or point `TURNOS_DEMO_IMAGE_ENV_FILE` at a
local environment file. No workstation-specific path is assumed.
"""
import argparse
import base64
import json
import os
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "packs", "example-town")
PHOTOS = os.path.join(PACK, "centro-photos")

MODEL = "gpt-image-2"
SIZE = "1536x1024"
OUT_W, OUT_H = 800, 600          # 4:3, the aspect tiles.mjs reserves

# A shared preamble keeps the five looking like one photographer's set
# rather than five stock images.
STYLE = (
    "Documentary architectural photograph, bright overcast midday light, "
    "eye-level from across the street, straight-on, 35mm, natural colour, "
    "no filter. A modern, well-resourced Argentine coastal town: a fully "
    "paved street with a proper kerb and painted road markings, paved "
    "sidewalks, trimmed street trees and landscaped low hedges — no dirt, "
    "sand or gravel anywhere in the frame. Contemporary healthcare "
    "architecture built in the last decade: clean rectilinear volumes, "
    "large glass curtain-wall sections, light-toned render or fibre-cement "
    "cladding, flat or gently sloped roofs, a paved and marked patient "
    "drop-off area. IMPORTANT: no readable text or lettering anywhere, no "
    "signage with words, no logos, no license plates, no recognisable "
    "faces, no people in the foreground."
)

CENTERS = {
    "sampleton-hospital": (
        "A modern municipal hospital: three storeys, a glass-and-white-panel "
        "facade, a large flat glazed canopy over the main entrance, a marked "
        "ambulance bay to one side, a paved forecourt with a few parked cars "
        "and a bicycle rack."),
    "riverside-clinic": (
        "A modern single-storey neighbourhood primary-care clinic: light grey "
        "fibre-cement cladding, a full-height glass entrance front, a flat "
        "roof with a slim overhang, a paved accessible ramp to the door, "
        "clipped low hedges along the front."),
    "lakeview-clinic": (
        "A modern single-storey neighbourhood primary-care clinic, different "
        "from the others: white render with a band of dark grey cladding, a "
        "covered entrance canopy on slim steel columns, a paved forecourt, "
        "young trees in raised planters."),
    "maternity-center": (
        "A modern maternal and child health centre: two storeys, pale blue "
        "and white panel cladding, large windows with white frames, a small "
        "fenced landscaped garden with a bench, wide glazed automatic "
        "entrance doors."),
    "diagnostic-center": (
        "A modern diagnostic-imaging and laboratory annexe: a compact "
        "single-storey glass-and-concrete block, a long horizontal ribbon "
        "window, a flat roof, a paved forecourt with clear parking bay "
        "markings."),
}


def _api_key():
    path = os.environ.get("TURNOS_DEMO_IMAGE_ENV_FILE", "")
    if path:
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("OPENAI_API_KEY="):
                        return line.split("=", 1)[1].strip().strip('"')
        except OSError:
            pass
    return os.environ.get("OPENAI_API_KEY", "")


def generate(prompt, key):
    body = json.dumps({
        "model": MODEL, "prompt": prompt, "size": SIZE, "n": 1,
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/images/generations", data=body,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.load(resp)
    return base64.b64decode(data["data"][0]["b64_json"])


def to_tile(raw_bytes, dest_base):
    """Center-crop to 4:3, write the .jpg the tiles use and a .webp beside it
    (the same pair Pinamar's centro-photos/ carries)."""
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    want = OUT_W / OUT_H
    if w / h > want:
        new_w = int(h * want)
        img = img.crop(((w - new_w) // 2, 0, (w - new_w) // 2 + new_w, h))
    else:
        new_h = int(w / want)
        img = img.crop((0, (h - new_h) // 2, w, (h - new_h) // 2 + new_h))
    img = img.resize((OUT_W, OUT_H), Image.LANCZOS)
    img.save(dest_base + ".jpg", "JPEG", quality=86, optimize=True)
    img.save(dest_base + ".webp", "WEBP", quality=82, method=6)


def write_info(slugs):
    """centros-info.json: the file site_centers._centers_web() reads a
    center's `photo` out of. Only the photo key here — the researched facts
    Pinamar's version carries have no fictional equivalent."""
    path = os.path.join(PACK, "centros-info.json")
    info = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            info = json.load(fh)
    info["_note"] = (
        "Sampleton es una localidad ficticia: estas fotos NO son de "
        "ningún edificio real — las genera scripts/gen_demo_center_photos.py "
        "con gpt-image-2, sin carteles ni nombres legibles. Fotos en "
        "/webdata/centro-photos/<slug>.webp.")
    for slug in slugs:
        entry = info.get(slug) or {}
        entry["photo"] = f"/webdata/centro-photos/{slug}.webp?v=2"
        entry["credit"] = "Imagen generada (localidad ficticia)"
        info[slug] = entry
    ordered = {"_note": info.pop("_note")}
    ordered.update({k: info[k] for k in sorted(info)})
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ordered, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"{path}: {len(slugs)} center(s).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="One center slug.")
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even if the image already exists.")
    args = parser.parse_args()

    slugs = [args.only] if args.only else list(CENTERS)
    unknown = [s for s in slugs if s not in CENTERS]
    if unknown:
        parser.error(f"unknown center(s): {', '.join(unknown)}")

    key = _api_key()
    if not key:
        print("No OPENAI_API_KEY; set it or TURNOS_DEMO_IMAGE_ENV_FILE.")
        sys.exit(1)
    os.makedirs(PHOTOS, exist_ok=True)

    for slug in slugs:
        base = os.path.join(PHOTOS, slug)
        if os.path.exists(base + ".webp") and not args.force:
            print(f"  {slug}: already there, skipping (--force to redo)")
            continue
        print(f"  {slug}: generating…", flush=True)
        to_tile(generate(f"{STYLE} {CENTERS[slug]}", key), base)
        print(f"  {slug}: {base}.jpg + .webp")

    write_info(list(CENTERS))


if __name__ == "__main__":
    main()
