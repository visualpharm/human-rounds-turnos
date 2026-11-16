#!/usr/bin/env python3
"""Build the municipal doctor directory sprite.

Sources (``medico-fotos.json`` in the configured private town pack):
  - reuse:   tile ya retocado con GPT en <workspace>/pinamar-turnos (mismo pipeline,
             ese repo's OWN webdata/photos.json + data/doctor-photo-batches/tiles/<sha24>.webp).
  - colmed9: foto oficial del Colegio de Médicos Distrito IX
             (servicios.colmed9.com.ar/fotos_matriculados/<matricula>.jpg).

Stages:
  --collect  copies reusable tiles into the private pack and downloads source
             images to ``data/medico-photos-src/`` (runtime data, not the repo).
  --sheet    builds a 4x3 contact sheet for missing portraits.
  --edit     sends the sheet to the image editor and cuts the returned tiles.
  --pack     packs all available private-pack tiles into the WebP sprite and
             JSON manifest.

The expensive step is ``--edit``. Packing is local and repeatable.
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
from app.core import data_packs, sitedata  # noqa: E402

PARENT = Path.home() / "projects" / "pinamar-turnos"

MEDICOS_PATH = Path(sitedata.path("medicos.json"))
FOTOS_MAP_PATH = Path(sitedata.path("medico-fotos.json"))
# Directories use the same private-pack resolver as runtime files.
TILE_OUT_DIR = Path(data_packs.private_pack_dir("ar-b-pinamar")) / "medico-photos"
SPRITE_PATH = Path(sitedata.path("medico-photo-sprite.webp"))
MANIFEST_PATH = Path(sitedata.path("medico-photo-sprite.json"))
SRC_DIR = ROOT / "data" / "medico-photos-src"

COLS = 4
ROWS = 3
SHEET_TILE = 384
SPRITE_TILE = 256
SPRITE_COLS = 6

PROMPT = """Edit this exact 4-column by 3-row doctor portrait contact sheet into a polished medical-directory avatar sheet.

Hard invariants:
- Preserve the exact grid, same number of people, same ordering, and one flat rectangular sheet. Leave empty tiles empty.
- Preserve each real person's identity. Do not replace, invent, remove, stylize, beautify, age-change, or caricature anyone.
- Keep glasses, facial hair, hairstyle, expression, and approximate clothing type.
- No text, labels, watermarks, borders, UI, rounded masks, or extra decoration.

Retouch goals:
- Passport-photo composition in every tile: full head visible, no cropped hair/chin, shoulders and a little chest visible, similar to formal ID or clinic profile photos.
- Keep the detected face approximately 40–45% of the tile height in every portrait; do not leave one person visibly smaller than the rest of the sheet.
- Leave a little more space above the hairline: roughly the same vertical distance as from the upper lip to the chin. Do not crop close to the top of the head.
- Align eyes to a consistent horizontal level across all tiles.
- Neutral, professional lighting: consistent white balance, exposure, contrast, and mild sharpening.
- If a person was caught mid-blink or with eyes closed, open their eyes naturally.
- If a person is wearing a surgical face mask in the source, KEEP that mask on — never invent the covered part of the face. Never add a mask to an unmasked person.
- Remove non-clinical props and artifacts: headphones, microphones, text or logo overlays; reconstruct hair and clothing naturally where they were.
- Clean off-white/light gray studio background. White or very light neutral clothing is OK if needed to make the set cohesive.

This will be clipped into tiny circular avatars, so prioritize readable faces, consistent scale, and natural portrait geometry.
"""


def _load_map() -> dict:
    data = json.loads(FOTOS_MAP_PATH.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _medico_ids() -> list[str]:
    ms = json.loads(MEDICOS_PATH.read_text(encoding="utf-8"))
    if isinstance(ms, dict):
        ms = ms.get("medicos", ms)
    return [m["id"] for m in ms]


def _sha24(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def collect() -> None:
    fmap = _load_map()
    TILE_OUT_DIR.mkdir(parents=True, exist_ok=True)
    SRC_DIR.mkdir(parents=True, exist_ok=True)
    photos_json = json.loads((PARENT / "webdata" / "photos.json").read_text(encoding="utf-8"))
    tiles_dir = PARENT / "data" / "doctor-photo-batches" / "tiles"
    for mid, spec in fmap.items():
        out = TILE_OUT_DIR / f"{mid}.webp"
        if out.exists():
            continue
        if "reuse" in spec:
            url = photos_json.get(spec["reuse"]) or ""
            tile = tiles_dir / f"{_sha24(url)}.webp"
            if not tile.is_file():
                print(f"MISSING reuse tile for {mid} ({spec['reuse']})", file=sys.stderr)
                continue
            out.write_bytes(tile.read_bytes())
            print(f"reused {mid}")
        elif "colmed9" in spec:
            raw = SRC_DIR / f"{mid}.jpg"
            if not raw.exists():
                url = f"https://servicios.colmed9.com.ar/fotos_matriculados/{spec['colmed9']}.jpg"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 SanaTurnos-photos"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    raw.write_bytes(r.read())
                print(f"downloaded {mid} <- colmed9 {spec['colmed9']}")
        elif "web" in spec:
            # foto pública verificada (ver _prov). lookaside.fbsbx.com sólo responde
            # imagen con User-Agent de crawler; normalmente el recorte curado ya
            # está en SRC_DIR y esto no corre.
            raw = SRC_DIR / f"{mid}.jpg"
            if not raw.exists():
                req = urllib.request.Request(spec["web"], headers={"User-Agent": "facebookexternalhit/1.1"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    raw.write_bytes(r.read())
                print(f"downloaded {mid} <- web")


def _pending_ids() -> list[str]:
    fmap = _load_map()
    return [mid for mid in fmap
            if not (TILE_OUT_DIR / f"{mid}.webp").exists()
            and (SRC_DIR / f"{mid}.jpg").exists()]


def sheet() -> None:
    ids = _pending_ids()
    if not ids:
        print("nothing pending")
        return
    manifest = {"columns": COLS, "rows": ROWS, "sheets": []}
    per = COLS * ROWS
    for si in range(math.ceil(len(ids) / per)):
        chunk = ids[si * per:(si + 1) * per]
        img = Image.new("RGB", (COLS * SHEET_TILE, ROWS * SHEET_TILE), "#f4f4f2")
        for i, mid in enumerate(chunk):
            src = ImageOps.exif_transpose(Image.open(SRC_DIR / f"{mid}.jpg")).convert("RGB")
            tile = ImageOps.fit(src, (SHEET_TILE, SHEET_TILE),
                                method=Image.Resampling.LANCZOS, centering=(0.5, 0.36))
            img.paste(tile, ((i % COLS) * SHEET_TILE, (i // COLS) * SHEET_TILE))
        out = SRC_DIR / f"sheet-{si + 1:02d}.png"
        img.save(out)
        manifest["sheets"].append({"file": out.name, "ids": chunk})
        print(f"wrote {out} ({len(chunk)} portraits)")
    (SRC_DIR / "sheet-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def edit() -> None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY is not set")
    manifest = json.loads((SRC_DIR / "sheet-manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["sheets"]:
        src = SRC_DIR / entry["file"]
        edited = SRC_DIR / f"edited-{entry['file']}"
        if not edited.exists():
            response = SRC_DIR / f"{src.stem}-response.json"
            cmd = [
                "curl", "-sS", "https://api.openai.com/v1/images/edits",
                "-H", f"Authorization: Bearer {key}",
                "-F", "model=gpt-image-2",
                "-F", f"image=@{src}",
                "-F", f"prompt={PROMPT}",
                "-F", f"size={COLS * SHEET_TILE}x{ROWS * SHEET_TILE}",
                "-F", "quality=high",
                "-F", "output_format=png",
                "-o", str(response),
            ]
            print(f"editing {src}")
            subprocess.run(cmd, check=True)
            data = json.loads(response.read_text(encoding="utf-8"))
            if data.get("error"):
                raise SystemExit(data["error"])
            edited.write_bytes(base64.b64decode(data["data"][0]["b64_json"]))
            print(f"wrote {edited}")
        img = Image.open(edited).convert("RGB")
        tw, th = img.width / COLS, img.height / ROWS
        for i, mid in enumerate(entry["ids"]):
            c, r = i % COLS, i // COLS
            tile = ImageOps.fit(
                img.crop((round(c * tw), round(r * th), round((c + 1) * tw), round((r + 1) * th))),
                (SHEET_TILE, SHEET_TILE), method=Image.Resampling.LANCZOS)
            out = TILE_OUT_DIR / f"{mid}.webp"
            tile.save(out, "WEBP", quality=94, method=6)
            print(f"cut {out}")


def pack() -> None:
    ids = [mid for mid in _medico_ids() if (TILE_OUT_DIR / f"{mid}.webp").exists()]
    rows = math.ceil(len(ids) / SPRITE_COLS)
    sprite = Image.new("RGB", (SPRITE_COLS * SPRITE_TILE, rows * SPRITE_TILE), "#f4f4f2")
    entries = {}
    for i, mid in enumerate(ids):
        tile = ImageOps.fit(Image.open(TILE_OUT_DIR / f"{mid}.webp").convert("RGB"),
                            (SPRITE_TILE, SPRITE_TILE),
                            method=Image.Resampling.LANCZOS, centering=(0.5, 0.48))
        x, y = (i % SPRITE_COLS) * SPRITE_TILE, (i // SPRITE_COLS) * SPRITE_TILE
        sprite.paste(tile, (x, y))
        entries[mid] = {"x": x, "y": y}
    sprite.save(SPRITE_PATH, "WEBP", quality=88, method=6)
    MANIFEST_PATH.write_text(json.dumps({
        "image": "/webdata/medico-photo-sprite.webp",
        "tile": SPRITE_TILE, "columns": SPRITE_COLS,
        "width": sprite.width, "height": sprite.height,
        "count": len(ids), "entries": entries,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"packed {len(ids)} portraits -> {SPRITE_PATH}")


def main() -> int:
    args = set(sys.argv[1:])
    if "--collect" in args:
        collect()
    if "--sheet" in args:
        sheet()
    if "--edit" in args:
        edit()
    if "--pack" in args:
        pack()
    if not args:
        print("stages: --collect --sheet --edit --pack (in that order)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
