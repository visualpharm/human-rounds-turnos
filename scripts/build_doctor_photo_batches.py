#!/usr/bin/env python3
"""Batch-normalize doctor portraits through GPT-edited contact sheets.

Workflow:
  1. --make-sources: download current registry photos and build 4x3 contact sheets.
  2. --edit: send each source sheet to the OpenAI image edit API and save edited sheets.
  3. --pack: pack edited sheets into a single web sprite + JSON manifest.

The expensive step is --edit. Packing is local and can be repeated while tuning
avatar scale/composition.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.core import data_packs  # noqa: E402
from app.patient_web import availability_directory  # noqa: E402

OUT_DIR = ROOT / "data" / "doctor-photo-batches"
SOURCE_DIR = OUT_DIR / "source"
EDITED_DIR = OUT_DIR / "edited"
STATIC_DIR = Path(data_packs.private_pack_dir("ar-b-pinamar"))
SPRITE_PATH = STATIC_DIR / "doctor-photo-sprite.webp"
MANIFEST_PATH = STATIC_DIR / "doctor-photo-sprite.json"
ZOOM_DIR = STATIC_DIR / "doctor-photos"
DELTA_SOURCE_DIR = OUT_DIR / "new-source"
DELTA_EDITED_DIR = OUT_DIR / "new-edited"
DELTA_MANIFEST_PATH = OUT_DIR / "new-manifest.json"
TILE_DIR = OUT_DIR / "tiles"

COLS = 4
ROWS = 3
SHEET_TILE = 384
SHEET_WIDTH = COLS * SHEET_TILE
SHEET_HEIGHT = ROWS * SHEET_TILE
SPRITE_TILE = 128
DISPLAY = 34

SOURCE_TRANSFORMS = {
    "sanchez-guadalupe": Image.Transpose.ROTATE_270,
}

PROMPT = """Edit this exact 4-column by 3-row doctor portrait contact sheet into a polished medical-directory avatar sheet.

Hard invariants:
- Preserve the exact grid, same number of people, same ordering, and one flat rectangular sheet.
- Preserve each real person's identity. Do not replace, invent, remove, stylize, beautify, age-change, or caricature anyone.
- Keep glasses, facial hair, hairstyle, expression, and approximate clothing type.
- No text, labels, watermarks, borders, UI, rounded masks, or extra decoration.

Retouch goals:
- Passport-photo composition in every tile: full head visible, no cropped hair/chin, shoulders and a little chest visible, similar to formal ID or clinic profile photos.
- Leave a little more space above the hairline: roughly the same vertical distance as from the upper lip to the chin. Do not crop close to the top of the head.
- Align eyes to a consistent horizontal level across all tiles.
- Align hairline/head top and chin level consistently across all tiles.
- Neutral, professional lighting: consistent white balance, exposure, contrast, and mild sharpening.
- Clean off-white/light gray studio background. White or very light neutral clothing is OK if needed to make the set cohesive. Avoid mixed old-photo lighting and rough cutout edges.

This will be clipped into tiny circular avatars, so prioritize readable faces, consistent scale, and natural portrait geometry.
"""


def _download(url: str) -> Image.Image:
    if url.startswith("/data/"):
        local_path = STATIC_DIR / url.removeprefix("/data/")
        return ImageOps.exif_transpose(Image.open(local_path)).convert("RGB")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 Sacoturno-photo-batch"},
    )
    with urllib.request.urlopen(req, timeout=25) as r:
        return ImageOps.exif_transpose(Image.open(r)).convert("RGB")


def _items():
    seen = {}
    for m in availability_directory.public_medicos():
        url = m.get("foto")
        if not url:
            continue
        item = seen.setdefault(
            url,
            {"url": url, "name": m["nombre"], "slugs": [], "names": []},
        )
        item["slugs"].append(m["slug"])
        item["names"].append(m["nombre"])
    return list(seen.values())


def _fix_source(img: Image.Image, item: dict) -> Image.Image:
    for slug in item.get("slugs") or []:
        transform = SOURCE_TRANSFORMS.get(slug)
        if transform is not None:
            return img.transpose(transform)
    return img


def _url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _tile_path(url: str) -> Path:
    return TILE_DIR / f"{_url_key(url)}.webp"


def _cache_current_zoom_tiles() -> int:
    if not MANIFEST_PATH.exists():
        return 0
    sprite_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = sprite_manifest.get("entries") or {}
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for item in _items():
        out = _tile_path(item["url"])
        if out.exists():
            continue
        for slug in item.get("slugs") or []:
            entry = entries.get(slug) or {}
            if entry.get("source") != item["url"]:
                continue
            zoom_path = STATIC_DIR / str(entry.get("zoom") or "").removeprefix("/data/")
            if not zoom_path.is_file():
                continue
            tile = ImageOps.fit(
                Image.open(zoom_path).convert("RGB"),
                (SHEET_TILE, SHEET_TILE),
                method=Image.Resampling.LANCZOS,
            )
            tile.save(out, "WEBP", quality=94, method=6)
            saved += 1
            break
    return saved


def _extract_tiles(manifest_path: Path, edited_dir: Path, *, overwrite: bool = False) -> int:
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_cols = int(manifest.get("columns") or COLS)
    source_rows = int(manifest.get("rows") or ROWS)
    per_sheet = source_cols * source_rows
    items = manifest.get("items") or []
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for sheet_idx, sheet_info in enumerate(manifest.get("sheets") or []):
        edited = edited_dir / Path(sheet_info["source"]).name
        if not edited.exists():
            continue
        sheet = Image.open(edited).convert("RGB")
        src_tile_width = sheet.width / source_cols
        src_tile_height = sheet.height / source_rows
        sheet_items = items[sheet_idx * per_sheet : (sheet_idx + 1) * per_sheet]
        for i, item in enumerate(sheet_items):
            out = _tile_path(item["url"])
            if out.exists() and not overwrite:
                continue
            col = i % source_cols
            row = i // source_cols
            bounds = (
                round(col * src_tile_width),
                round(row * src_tile_height),
                round((col + 1) * src_tile_width),
                round((row + 1) * src_tile_height),
            )
            tile = ImageOps.fit(
                sheet.crop(bounds),
                (SHEET_TILE, SHEET_TILE),
                method=Image.Resampling.LANCZOS,
            )
            tile.save(out, "WEBP", quality=94, method=6)
            saved += 1
    return saved


def make_new_sources() -> None:
    cached = _cache_current_zoom_tiles()
    cached += _extract_tiles(OUT_DIR / "manifest.json", EDITED_DIR)
    cached += _extract_tiles(DELTA_MANIFEST_PATH, DELTA_EDITED_DIR)
    if cached:
        print(f"cached {cached} processed portrait tiles")
    items = [item for item in _items() if not _tile_path(item["url"]).exists()]
    DELTA_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    for stale in DELTA_SOURCE_DIR.glob("new-doctors-*.png"):
        stale.unlink()
    manifest = {"columns": COLS, "rows": ROWS, "tile": SHEET_TILE, "items": items, "sheets": []}
    per_sheet = COLS * ROWS
    for sheet_idx in range(math.ceil(len(items) / per_sheet)):
        sheet_items = items[sheet_idx * per_sheet : (sheet_idx + 1) * per_sheet]
        sheet = Image.new("RGB", (SHEET_WIDTH, SHEET_HEIGHT), "#f4f4f2")
        for i, item in enumerate(sheet_items):
            try:
                img = _fix_source(_download(item["url"]), item)
            except Exception as exc:  # noqa: BLE001
                print(f"skip {item['name']}: {exc}", file=sys.stderr)
                continue
            tile = ImageOps.fit(
                img,
                (SHEET_TILE, SHEET_TILE),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.36),
            )
            x = (i % COLS) * SHEET_TILE
            y = (i // COLS) * SHEET_TILE
            sheet.paste(tile, (x, y))
        out = DELTA_SOURCE_DIR / f"new-doctors-{sheet_idx + 1:02d}.png"
        sheet.save(out)
        manifest["sheets"].append(
            {"index": sheet_idx, "source": "/" + str(out.relative_to(ROOT)), "count": len(sheet_items)}
        )
        print(f"wrote {out}")
    DELTA_MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"{len(items)} new portraits in {len(manifest['sheets'])} sheets")


def ingest_new_edits() -> None:
    expected = json.loads(DELTA_MANIFEST_PATH.read_text(encoding="utf-8"))
    missing = [
        DELTA_EDITED_DIR / Path(sheet["source"]).name
        for sheet in expected.get("sheets") or []
        if not (DELTA_EDITED_DIR / Path(sheet["source"]).name).exists()
    ]
    if missing:
        raise SystemExit("Missing edited sheets: " + ", ".join(str(path) for path in missing))
    saved = _extract_tiles(DELTA_MANIFEST_PATH, DELTA_EDITED_DIR, overwrite=True)
    print(f"cached {saved} newly edited portrait tiles")


def pack_current(avatar_scale: float = 1.0) -> None:
    items = _items()
    missing = [item for item in items if not _tile_path(item["url"]).exists()]
    if missing:
        raise SystemExit(
            f"Missing {len(missing)} processed tiles. Run --make-new-sources, edit them, and --ingest-new."
        )
    rows = math.ceil(len(items) / COLS)
    sprite = Image.new("RGB", (COLS * SPRITE_TILE, rows * SPRITE_TILE), "#f4f4f2")
    entries = {}
    replaced_urls = set()
    if DELTA_MANIFEST_PATH.exists():
        delta_manifest = json.loads(DELTA_MANIFEST_PATH.read_text(encoding="utf-8"))
        replaced_urls = {item["url"] for item in delta_manifest.get("items") or []}
    ZOOM_DIR.mkdir(parents=True, exist_ok=True)
    for global_i, item in enumerate(items):
        high_tile = Image.open(_tile_path(item["url"])).convert("RGB")
        tile = ImageOps.fit(
            high_tile,
            (SPRITE_TILE, SPRITE_TILE),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.48),
        )
        if avatar_scale < 0.999:
            small = tile.resize(
                (round(SPRITE_TILE * avatar_scale), round(SPRITE_TILE * avatar_scale)),
                Image.Resampling.LANCZOS,
            )
            bg = tile.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
            canvas = Image.new("RGB", (SPRITE_TILE, SPRITE_TILE), bg)
            canvas.paste(small, (round((SPRITE_TILE - small.width) / 2), round((SPRITE_TILE - small.height) / 2)))
            tile = canvas
        x = (global_i % COLS) * SPRITE_TILE
        y = (global_i // COLS) * SPRITE_TILE
        sprite.paste(tile, (x, y))
        css_scale = DISPLAY / SPRITE_TILE
        for slug in item["slugs"]:
            zoom_path = ZOOM_DIR / f"{slug}.webp"
            if item["url"] in replaced_urls or not zoom_path.exists():
                high_tile.save(zoom_path, "WEBP", quality=90, method=6)
            entries[slug] = {
                "image": "/data/doctor-photo-sprite.webp",
                "zoom": f"/data/doctor-photos/{slug}.webp",
                "source": item["url"],
                "x": x,
                "y": y,
                "bg_size": f"{sprite.width * css_scale:.3f}px {sprite.height * css_scale:.3f}px",
                "bg_pos": f"{-x * css_scale:.3f}px {-y * css_scale:.3f}px",
            }
    sprite.save(SPRITE_PATH, "WEBP", quality=88, method=6)
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "image": "/data/doctor-photo-sprite.webp",
                "tile": SPRITE_TILE,
                "display": DISPLAY,
                "width": sprite.width,
                "height": sprite.height,
                "columns": COLS,
                "count": len(items),
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"wrote current {SPRITE_PATH} and {MANIFEST_PATH}")


def make_sources() -> None:
    items = _items()
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"columns": COLS, "rows": ROWS, "tile": SHEET_TILE, "items": items, "sheets": []}
    per_sheet = COLS * ROWS
    for sheet_idx in range(math.ceil(len(items) / per_sheet)):
        sheet_items = items[sheet_idx * per_sheet : (sheet_idx + 1) * per_sheet]
        sheet = Image.new("RGB", (SHEET_WIDTH, SHEET_HEIGHT), "#f4f4f2")
        for i, item in enumerate(sheet_items):
            try:
                img = _download(item["url"])
            except Exception as e:  # noqa: BLE001
                print(f"skip {item['name']}: {e}", file=sys.stderr)
                continue
            img = _fix_source(img, item)
            tile = ImageOps.fit(img, (SHEET_TILE, SHEET_TILE), method=Image.Resampling.LANCZOS, centering=(0.5, 0.36))
            x = (i % COLS) * SHEET_TILE
            y = (i // COLS) * SHEET_TILE
            sheet.paste(tile, (x, y))
        out = SOURCE_DIR / f"doctors-{sheet_idx + 1:02d}.png"
        sheet.save(out)
        manifest["sheets"].append({"index": sheet_idx, "source": "/" + str(out.relative_to(ROOT)), "count": len(sheet_items)})
        print(f"wrote {out}")
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def edit_sources(limit: int | None = None, force: bool = False) -> None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY is not set")
    EDITED_DIR.mkdir(parents=True, exist_ok=True)
    sources = sorted(SOURCE_DIR.glob("doctors-*.png"))
    if not sources:
        raise SystemExit("No source sheets. Run --make-sources first.")
    for src in sources[:limit]:
        out = EDITED_DIR / src.name
        if out.exists() and not force:
            print(f"skip existing {out}")
            continue
        if out.exists() and force:
            out.unlink()
        response = OUT_DIR / f"{src.stem}-response.json"
        cmd = [
            "curl", "-sS", "https://api.openai.com/v1/images/edits",
            "-H", f"Authorization: Bearer {key}",
            "-F", "model=gpt-image-2",
            "-F", f"image=@{src}",
            "-F", f"prompt={PROMPT}",
            "-F", f"size={SHEET_WIDTH}x{SHEET_HEIGHT}",
            "-F", "quality=high",
            "-F", "output_format=png",
            "-o", str(response),
        ]
        print(f"editing {src}")
        subprocess.run(cmd, check=True)
        data = json.loads(response.read_text(encoding="utf-8"))
        if data.get("error"):
            raise SystemExit(data["error"])
        b64 = data["data"][0].get("b64_json")
        if not b64:
            raise SystemExit(f"No image returned for {src}")
        out.write_bytes(base64.b64decode(b64))
        print(f"wrote {out}")


def pack(avatar_scale: float = 1.0) -> None:
    manifest_path = OUT_DIR / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit("No manifest. Run --make-sources first.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items = manifest["items"]
    source_cols = int(manifest.get("columns") or COLS)
    source_rows = int(manifest.get("rows") or ROWS)
    per_sheet = source_cols * source_rows
    rows = math.ceil(len(items) / source_cols)
    sprite = Image.new("RGB", (source_cols * SPRITE_TILE, rows * SPRITE_TILE), "#f4f4f2")
    entries = {}
    ZOOM_DIR.mkdir(parents=True, exist_ok=True)
    for stale in ZOOM_DIR.glob("*.webp"):
        stale.unlink()
    for sheet_idx, sheet_info in enumerate(manifest["sheets"]):
        edited = EDITED_DIR / Path(sheet_info["source"]).name
        if not edited.exists():
            raise SystemExit(f"Missing edited sheet: {edited}")
        sheet = Image.open(edited).convert("RGB")
        src_tile = sheet.width // source_cols
        sheet_items = items[sheet_idx * per_sheet : (sheet_idx + 1) * per_sheet]
        for i, item in enumerate(sheet_items):
            global_i = sheet_idx * per_sheet + i
            sx = (i % source_cols) * src_tile
            sy = (i // source_cols) * src_tile
            high_tile = sheet.crop((sx, sy, sx + src_tile, sy + src_tile))
            tile = ImageOps.fit(high_tile, (SPRITE_TILE, SPRITE_TILE), method=Image.Resampling.LANCZOS, centering=(0.5, 0.48))
            if avatar_scale < 0.999:
                small = tile.resize((round(SPRITE_TILE * avatar_scale), round(SPRITE_TILE * avatar_scale)), Image.Resampling.LANCZOS)
                bg = tile.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
                canvas = Image.new("RGB", (SPRITE_TILE, SPRITE_TILE), bg)
                canvas.paste(small, (round((SPRITE_TILE - small.width) / 2), round((SPRITE_TILE - small.height) / 2)))
                tile = canvas
            x = (global_i % source_cols) * SPRITE_TILE
            y = (global_i // source_cols) * SPRITE_TILE
            sprite.paste(tile, (x, y))
            css_scale = DISPLAY / SPRITE_TILE
            for slug in item["slugs"]:
                zoom_path = ZOOM_DIR / f"{slug}.webp"
                high_tile.save(zoom_path, "WEBP", quality=90, method=6)
                entries[slug] = {
                    "image": "/data/doctor-photo-sprite.webp",
                    "zoom": f"/data/doctor-photos/{slug}.webp",
                    "x": x,
                    "y": y,
                    "bg_size": f"{sprite.width * css_scale:.3f}px {sprite.height * css_scale:.3f}px",
                    "bg_pos": f"{-x * css_scale:.3f}px {-y * css_scale:.3f}px",
                }
    sprite.save(SPRITE_PATH, "WEBP", quality=88, method=6)
    MANIFEST_PATH.write_text(
        json.dumps(
            {
                "image": "/data/doctor-photo-sprite.webp",
                "tile": SPRITE_TILE,
                "display": DISPLAY,
                "width": sprite.width,
                "height": sprite.height,
                "columns": source_cols,
                "count": len(items),
                "entries": entries,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {SPRITE_PATH} and {MANIFEST_PATH}")


def main() -> int:
    args = set(sys.argv[1:])
    if "--make-sources" in args:
        make_sources()
    if "--make-new-sources" in args:
        make_new_sources()
    if "--edit" in args:
        limit = None
        for arg in sys.argv[1:]:
            if arg.startswith("--edit-limit="):
                limit = int(arg.split("=", 1)[1])
        edit_sources(limit, force="--force-edit" in args)
    if "--pack" in args:
        scale = 1.0
        for arg in sys.argv[1:]:
            if arg.startswith("--avatar-scale="):
                scale = float(arg.split("=", 1)[1])
        pack(scale)
    if "--ingest-new" in args:
        ingest_new_edits()
    if "--pack-current" in args:
        scale = 1.0
        for arg in sys.argv[1:]:
            if arg.startswith("--avatar-scale="):
                scale = float(arg.split("=", 1)[1])
        pack_current(scale)
    if not args:
        print(
            "Use --make-sources/--edit/--pack for a full rebuild, or "
            "--make-new-sources then --ingest-new/--pack-current for incremental photos."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
