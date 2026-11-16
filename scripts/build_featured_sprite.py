#!/usr/bin/env python3
"""Build the homepage featured-doctor sprite.

The homepage shows the same doctors returned by the availability directory.
This script downloads those source portraits, normalizes crop/color, packs them
into one WebP sprite, and writes a small manifest used by the API.
"""

from __future__ import annotations

import io
import json
import math
import hashlib
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps, ImageStat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.patient_web import availability_directory  # noqa: E402

OUT_IMG = ROOT / "data" / "featured-medic-sprite.webp"
OUT_JSON = ROOT / "data" / "featured-medic-sprite.json"
GPT_SHEET = ROOT / "data" / "featured-medic-gpt-sheet.png"
TILE = 96
DISPLAY = 34
COLS = 8
SOURCE_SHEET = ROOT / "data" / "featured-medic-source-sheet.png"
SOURCE_TILE = 256
BACKGROUNDS = [
    "#ebe7dc", "#e4ebea", "#ece7ef", "#eee9df", "#e7eadf",
    "#e9e4df", "#e4e8ee", "#eee7e2", "#e7ebe5", "#ebe8ed",
]

# Per-cell corrections from visual review. Indexes are zero-based in the packed
# 8-column sheet: row 1 col 1 = 0, row 2 col 1 = 8, etc. Keep this sparse:
# the default face-box crop does the main alignment work.
CELL_ADJUSTMENTS = {}


def _try_cv2():
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:
        return None


def _download(url: str) -> Image.Image:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Sacoturno-image-build"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    return ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")


def _white_balance(img: Image.Image) -> Image.Image:
    stat = ImageStat.Stat(img)
    means = [max(1.0, v) for v in stat.mean[:3]]
    target = sum(means) / 3.0
    channels = img.split()
    fixed = [ch.point(lambda p, f=target / means[i]: max(0, min(255, int(p * f)))) for i, ch in enumerate(channels)]
    return Image.merge("RGB", fixed)


def _improve(img: Image.Image, cv2=None) -> Image.Image:
    img = _white_balance(img)
    if cv2 is not None:
        try:
            import numpy as np

            arr = np.array(img)
            lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=1.35, tileGridSize=(8, 8))
            l = clahe.apply(l)
            arr = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB)
            img = Image.fromarray(arr)
        except Exception:
            pass
    img = ImageOps.autocontrast(img, cutoff=0.25)
    img = ImageEnhance.Color(img).enhance(0.98)
    img = ImageEnhance.Contrast(img).enhance(1.04)
    img = ImageEnhance.Sharpness(img).enhance(1.06)
    return img


def _detect_anchor(img: Image.Image, cv2):
    """Return (eye_x, eye_y, eye_dist, face_w) in source pixels, or None."""
    if cv2 is None:
        return None
    import numpy as np

    arr = np.array(img)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    face_xml = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    eye_xml = cv2.data.haarcascades + "haarcascade_eye.xml"
    face_cascade = cv2.CascadeClassifier(face_xml)
    eye_cascade = cv2.CascadeClassifier(eye_xml)
    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(28, 28))
    if len(faces) == 0:
        return None
    x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
    roi = gray[y : y + int(h * 0.58), x : x + w]
    eyes = eye_cascade.detectMultiScale(roi, scaleFactor=1.08, minNeighbors=5, minSize=(8, 8))
    pts = []
    for ex, ey, ew, eh in eyes:
        cx, cy = x + ex + ew / 2.0, y + ey + eh / 2.0
        if y + h * 0.14 <= cy <= y + h * 0.58:
            pts.append((cx, cy, ew * eh))
    pts.sort(key=lambda p: p[2], reverse=True)
    if len(pts) >= 2:
        pair = sorted(pts[:2], key=lambda p: p[0])
        eye_x = (pair[0][0] + pair[1][0]) / 2.0
        eye_y = (pair[0][1] + pair[1][1]) / 2.0
        eye_dist = max(1.0, abs(pair[1][0] - pair[0][0]))
        y_delta = abs(pair[0][1] - pair[1][1])
        if w * 0.25 <= eye_dist <= w * 0.68 and y_delta <= h * 0.12:
            return eye_x, eye_y, eye_dist, float(w), float(x), float(y), float(w), float(h)
    return x + w / 2.0, y + h * 0.38, max(1.0, w * 0.43), float(w), float(x), float(y), float(w), float(h)


def _crop_to_tile(img: Image.Image, cv2, bg: str) -> Image.Image:
    img = _improve(img, cv2)
    anchor = _detect_anchor(img, cv2)
    if anchor:
        eye_x, eye_y, eye_dist, face_w, face_x, face_y, box_w, box_h = anchor
        desired_face_h = TILE * 0.88
        desired_face_w = TILE * 0.72
        desired_eye_y = TILE * 0.38
        scale_by_box = max(desired_face_h / box_h, desired_face_w / box_w)
        scale_by_eyes = (TILE * 0.28) / eye_dist
        scale = (scale_by_box * 0.78) + (scale_by_eyes * 0.22)
        rw, rh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
        resized = img.resize((rw, rh), Image.Resampling.LANCZOS)
        face_center_x = (face_x + box_w / 2) * scale
        eye_center_x = eye_x * scale
        crop_left = ((face_center_x * 0.65) + (eye_center_x * 0.35)) - TILE / 2.0
        face_top_crop = face_y * scale - TILE * 0.06
        eye_crop = eye_y * scale - desired_eye_y
        crop_top = (face_top_crop * 0.58) + (eye_crop * 0.42)
        canvas = Image.new("RGB", (TILE, TILE), bg)
        canvas.paste(resized, (round(-crop_left), round(-crop_top)))
        return canvas
    fitted = ImageOps.fit(img, (TILE, TILE), method=Image.Resampling.LANCZOS, centering=(0.5, 0.37))
    return fitted


def _adjust_tile(tile: Image.Image, bg: str, adjustment: dict | None) -> Image.Image:
    if not adjustment:
        return tile
    scale = float(adjustment.get("scale", 1.0))
    dx = float(adjustment.get("dx", 0.0)) * TILE
    dy = float(adjustment.get("dy", 0.0)) * TILE
    if abs(scale - 1.0) < 0.001 and abs(dx) < 0.001 and abs(dy) < 0.001:
        return tile

    resized = tile.resize(
        (max(1, round(TILE * scale)), max(1, round(TILE * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGB", (TILE, TILE), bg)
    x = round((TILE - resized.width) / 2 + dx)
    y = round((TILE - resized.height) / 2 + dy)
    canvas.paste(resized, (x, y))
    return canvas


def _featured_faces():
    seen = set()
    faces = []
    for group in availability_directory.especialidades_tree().get("groups", [])[:6]:
        for face in group.get("fotos") or []:
            slug = face.get("slug")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            faces.append(face)
    return faces


def _build_source_sheet(faces, cv2) -> None:
    rows = math.ceil(len(faces) / COLS)
    sheet = Image.new("RGB", (COLS * SOURCE_TILE, rows * SOURCE_TILE), "#ebe7dc")
    for i, face in enumerate(faces):
        src = _download(face["foto"])
        # Keep the larger source sheet less aggressively cut out; it is the
        # image-model edit target and needs natural face/hair context.
        global TILE
        old_tile = TILE
        TILE = SOURCE_TILE
        try:
            bg = BACKGROUNDS[i % len(BACKGROUNDS)]
            tile = _crop_to_tile(src, cv2, bg)
            tile = _adjust_tile(tile, bg, CELL_ADJUSTMENTS.get(i))
        finally:
            TILE = old_tile
        x = (i % COLS) * SOURCE_TILE
        y = (i // COLS) * SOURCE_TILE
        sheet.paste(tile, (x, y))
    SOURCE_SHEET.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(SOURCE_SHEET)


def _pack_sheet(faces, sheet_path: Path) -> int:
    sheet = Image.open(sheet_path).convert("RGB")
    src_tile = sheet.width // COLS
    if src_tile <= 0:
        raise SystemExit(f"Bad sheet width: {sheet_path}")
    rows = math.ceil(len(faces) / COLS)
    sprite = Image.new("RGB", (COLS * TILE, rows * TILE), (239, 238, 234))
    entries = {}
    for i, face in enumerate(faces):
        sx = (i % COLS) * src_tile
        sy = (i // COLS) * src_tile
        tile = sheet.crop((sx, sy, sx + src_tile, sy + src_tile))
        tile = ImageOps.fit(tile, (TILE, TILE), method=Image.Resampling.LANCZOS, centering=(0.5, 0.48))
        # GPT-edited sheets should already have passport-photo composition:
        # full head plus some chest. Keep the packer neutral so the same edited
        # sheet can be judged directly in the UI.
        passport_scale = 1.0
        small = tile.resize(
            (round(TILE * passport_scale), round(TILE * passport_scale)),
            Image.Resampling.LANCZOS,
        )
        bg = tile.resize((1, 1), Image.Resampling.BOX).resize((TILE, TILE))
        canvas = Image.new("RGB", (TILE, TILE), bg.getpixel((0, 0)))
        canvas.paste(small, (round((TILE - small.width) / 2), round((TILE - small.height) / 2) + 3))
        tile = canvas
        x = (i % COLS) * TILE
        y = (i // COLS) * TILE
        sprite.paste(tile, (x, y))
        css_scale = DISPLAY / TILE
        entries[face["slug"]] = {
            "x": x,
            "y": y,
            "bg_size": f"{sprite.width * css_scale:.3f}px {sprite.height * css_scale:.3f}px",
            "bg_pos": f"{-x * css_scale:.3f}px {-y * css_scale:.3f}px",
        }
    OUT_IMG.parent.mkdir(parents=True, exist_ok=True)
    sprite.save(OUT_IMG, "WEBP", quality=88, method=6)
    sprite_hash = hashlib.sha256(OUT_IMG.read_bytes()).hexdigest()[:10]
    manifest = {
        "image": f"/data/featured-medic-sprite.webp?v={sprite_hash}",
        "tile": TILE,
        "display": DISPLAY,
        "columns": COLS,
        "source": "/" + str(sheet_path.relative_to(ROOT)),
        "source_tile": src_tile,
        "count": len(faces),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    OUT_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_IMG} and {OUT_JSON} from {sheet_path} ({len(faces)} portraits)")
    return 0


def main() -> int:
    cv2 = _try_cv2()
    faces = _featured_faces()
    if not faces:
        raise SystemExit("No featured faces found")
    if "--source-sheet" in sys.argv:
        _build_source_sheet(faces, cv2)
        print(f"Wrote {SOURCE_SHEET} ({len(faces)} portraits)")
        return 0
    if "--pack-gpt-sheet" in sys.argv:
        return _pack_sheet(faces, GPT_SHEET)

    rows = math.ceil(len(faces) / COLS)
    sprite = Image.new("RGB", (COLS * TILE, rows * TILE), (239, 238, 234))
    entries = {}
    for i, face in enumerate(faces):
        bg = BACKGROUNDS[i % len(BACKGROUNDS)]
        tile = _crop_to_tile(_download(face["foto"]), cv2, bg)
        tile = _adjust_tile(tile, bg, CELL_ADJUSTMENTS.get(i))
        x = (i % COLS) * TILE
        y = (i // COLS) * TILE
        sprite.paste(tile, (x, y))
        css_scale = DISPLAY / TILE
        entries[face["slug"]] = {
            "x": x,
            "y": y,
            "bg_size": f"{sprite.width * css_scale:.3f}px {sprite.height * css_scale:.3f}px",
            "bg_pos": f"{-x * css_scale:.3f}px {-y * css_scale:.3f}px",
        }

    OUT_IMG.parent.mkdir(parents=True, exist_ok=True)
    sprite.save(OUT_IMG, "WEBP", quality=86, method=6)
    sprite_hash = hashlib.sha256(OUT_IMG.read_bytes()).hexdigest()[:10]
    manifest = {
        "image": f"/data/featured-medic-sprite.webp?v={sprite_hash}",
        "tile": TILE,
        "display": DISPLAY,
        "columns": COLS,
        "count": len(faces),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "entries": entries,
    }
    OUT_JSON.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUT_IMG} and {OUT_JSON} ({len(faces)} portraits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
