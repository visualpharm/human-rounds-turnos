#!/usr/bin/env python3
"""Download each center's Google Places photos from the private town pack.

The business-info snapshots are converted into gallery images and merged into
the selected pack's ``centros-galeria.json``.

Idempotent: skips files already on disk and gallery entries already present.
The tracked media_url has its `key=` query param redacted (`key=REDACTED`) —
this script substitutes the real Places API key from GOOGLE_PLACES_API_KEY
(env / .env) before requesting; maxWidthPx caps the size.
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core import data_packs, sitedata  # noqa: E402

PACK_DIR = Path(data_packs.private_pack_dir("ar-b-pinamar"))
BRILA_DIR = PACK_DIR / "centros-brila-info"
OUT_DIR = PACK_DIR / "centro-photos" / "gallery"
GALERIA_PATH = Path(sitedata.path("centros-galeria.json"))

MAX_POR_CENTRO = 8
MAX_WIDTH = 1400


def _con_key_real(url):
    """Substitute the tracked key=REDACTED (or any key=...) with the real
    Places API key from the environment. Raises with a clear message if the
    key isn't set — never silently downloads without one."""
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "GOOGLE_PLACES_API_KEY no está seteada (ver .env.example) — "
            "clave en credential vault AI: 'Google Places API key — human-rounds scripts'"
        )
    return re.sub(r"key=[^&]*", f"key={api_key}", url)


def descargar(url, dest):
    # el media_url sirve el JPEG directo; maxWidthPx da 400 en algunas fotos,
    # así que se intenta con tope y se cae a la URL pelada
    url = _con_key_real(url)
    data = None
    for candidato in (f"{url}?maxWidthPx={MAX_WIDTH}", url):
        request = urllib.request.Request(candidato, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                data = response.read()
            break
        except Exception:
            continue
    if not data:
        raise ValueError("descarga fallida")
    if not data[:3] == b"\xff\xd8\xff":
        raise ValueError(f"no es JPEG ({len(data)} bytes)")
    dest.write_bytes(data)
    return len(data)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    galeria = json.loads(GALERIA_PATH.read_text(encoding="utf-8"))

    for path in sorted(BRILA_DIR.glob("*.json")):
        slug = path.stem
        data = json.loads(path.read_text(encoding="utf-8"))
        fotos = (data.get("photos") or [])[:MAX_POR_CENTRO]
        entradas = galeria.setdefault(slug, [])
        existentes = {e.get("src") for e in entradas}
        bajadas = 0
        for i, foto in enumerate(fotos, start=1):
            url = foto.get("media_url") or foto.get("name")
            if not url or not url.startswith("http"):
                continue
            nombre = f"{slug}-b{i}.jpg"
            src = f"/webdata/centro-photos/gallery/{nombre}"
            if src in existentes:
                continue
            dest = OUT_DIR / nombre
            if not dest.exists():
                try:
                    size = descargar(url, dest)
                except Exception as exc:  # una foto rota no frena el resto
                    print(f"{slug} #{i}: ERROR {exc}", file=sys.stderr)
                    continue
                print(f"{slug} #{i}: {size // 1024} KB")
            autor = ((foto.get("authorAttributions") or [{}])[0]).get("displayName") or ""
            entradas.append({"src": src,
                             "credit": (f"{autor} · Google Maps" if autor else "Google Maps")})
            existentes.add(src)
            bajadas += 1
        if bajadas:
            print(f"{slug}: +{bajadas} fotos en la galería")

    GALERIA_PATH.write_text(
        json.dumps(galeria, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("centros-galeria.json actualizado")


if __name__ == "__main__":
    main()
