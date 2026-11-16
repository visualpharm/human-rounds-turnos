#!/usr/bin/env python3
"""
fetch_resenas.py — descarga el corpus COMPLETO de reseñas de Google Maps de cada
centro de salud (webdata/centros-salud.json) vía el actor de Apify
compass~google-maps-reviews-scraper (mismo actor y forma de input que gatto-nero:
scripts/fetch-utopia-competitor-reviews.js).

Fuente primaria: Apify (place_id, tomado de
the configured private town pack's centros-brila-info/<slug>.json, que ya
trae el Place ID embebido en photo_url/reviews[].name como
"places/<PLACE_ID>/...").
Fallback: serper.dev /reviews (por cid) si Apify no está disponible o falla.

Salida por centro (snapshot crudo, con fecha de descarga):
  <private-town-pack>/fuentes/resenas-raw/<slug>.json

Uso:
  python3 scripts/fetch_resenas.py                 # todos los centros
  python3 scripts/fetch_resenas.py --centro hospital-pinamar
  APIFY_API_KEY=... python3 scripts/fetch_resenas.py
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from app.core import data_packs, sitedata  # noqa: E402

PINAMAR_PACK = data_packs.private_pack_dir("ar-b-pinamar")
BRILA_INFO_DIR = os.path.join(PINAMAR_PACK, "centros-brila-info")
CENTROS_FILE = sitedata.path("centros-salud.json")
OUT_DIR = os.path.join(PINAMAR_PACK, "fuentes", "resenas-raw")

ACTOR = "compass~google-maps-reviews-scraper"
MAX_REVIEWS = int(os.environ.get("TURNOS_RESENAS_MAX", "500"))


def log(msg):
    print(msg, flush=True)


def apify_token():
    return (
        os.environ.get("APIFY_API_KEY")
        or os.environ.get("APIFY_TOKEN")
        or ""
    ).strip()


def serper_key():
    return (os.environ.get("SERPER_API_KEY") or "").strip()


def http_json(url, method="GET", headers=None, body=None, timeout=60):
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            payload = raw.decode("utf-8", "replace")
        return e.code, payload


def cargar_centros():
    with open(CENTROS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("centros", [])


def place_id_de_brila(slug):
    """El Place ID de Google viene embebido en centros-brila-info/<slug>.json,
    dentro de photo_url o de reviews[].name: 'places/<PLACE_ID>/...'."""
    path = os.path.join(BRILA_INFO_DIR, f"{slug}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return None
    candidatos = []
    if d.get("photo_url"):
        candidatos.append(d["photo_url"])
    for r in d.get("reviews", []) or []:
        if r.get("name"):
            candidatos.append(r["name"])
    for c in candidatos:
        m = re.search(r"places/([^/]+)/", c)
        if m:
            return m.group(1)
    return None


def cid_de_maps_url(maps_url):
    if not maps_url:
        return None
    m = re.search(r"cid=(\d+)", maps_url)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Apify — mismo shape de input que gatto-nero/scripts/fetch-utopia-competitor-reviews.js
# ---------------------------------------------------------------------------

def apify_run_reseñas(token, place_id, max_reviews=MAX_REVIEWS):
    input_body = {
        "startUrls": [{"url": f"https://www.google.com/maps/place/?q=place_id:{place_id}"}],
        "maxReviews": max_reviews,
        "reviewsSort": "newest",
        "language": "es",
        "personalData": True,
    }
    url = f"https://api.apify.com/v2/acts/{ACTOR}/runs?token={token}"
    status, resp = http_json(url, method="POST", body=input_body)
    if status not in (200, 201) or not resp or not resp.get("data", {}).get("id"):
        raise RuntimeError(f"apify run start failed HTTP {status}: {str(resp)[:300]}")
    d = resp["data"]
    return d["id"], d["defaultDatasetId"]


def apify_wait_run(token, run_id, timeout_s=30 * 60, poll_s=10):
    url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={token}"
    waited = 0
    while waited < timeout_s:
        status, resp = http_json(url)
        st = resp and resp.get("data", {}).get("status")
        if st == "SUCCEEDED":
            return resp["data"]
        if st in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError(f"apify run {st}")
        time.sleep(poll_s)
        waited += poll_s
    raise RuntimeError("apify poll timeout")


def apify_dataset_items(token, dataset_id):
    url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={token}&format=json&clean=true"
    status, resp = http_json(url)
    if not isinstance(resp, list):
        raise RuntimeError(f"apify dataset fetch HTTP {status}: {str(resp)[:300]}")
    return resp


def fetch_apify(slug, place_id, token):
    log(f"  [apify] {slug}: iniciando run para {place_id}")
    run_id, dataset_id = apify_run_reseñas(token, place_id)
    log(f"  [apify] {slug}: run {run_id}, esperando…")
    apify_wait_run(token, run_id)
    items = apify_dataset_items(token, dataset_id)
    log(f"  [apify] {slug}: {len(items)} reseñas crudas")
    return items


# ---------------------------------------------------------------------------
# serper.dev fallback (POST /reviews, por cid)
# ---------------------------------------------------------------------------

def fetch_serper(slug, cid, key):
    all_reviews = []
    next_page_token = None
    for _ in range(20):  # tope de seguridad
        body = {"cid": cid, "gl": "ar", "hl": "es"}
        if next_page_token:
            body["nextPageToken"] = next_page_token
        status, resp = http_json(
            "https://google.serper.dev/reviews",
            method="POST",
            headers={"X-API-KEY": key},
            body=body,
        )
        if status != 200 or not resp:
            log(f"  [serper] {slug}: HTTP {status} — {str(resp)[:200]}")
            break
        page = resp.get("reviews", []) or []
        all_reviews.extend(page)
        next_page_token = resp.get("nextPageToken")
        if not next_page_token or not page:
            break
        time.sleep(0.5)
    log(f"  [serper] {slug}: {len(all_reviews)} reseñas crudas")
    return all_reviews


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    solo_centro = None
    if "--centro" in args:
        solo_centro = args[args.index("--centro") + 1]

    os.makedirs(OUT_DIR, exist_ok=True)
    centros = cargar_centros()
    token = apify_token()
    skey = serper_key()

    if not token:
        log("AVISO: no se encontró APIFY_API_KEY/APIFY_TOKEN en el entorno.")
    if not skey:
        log("AVISO: no se encontró SERPER_API_KEY en el entorno (fallback deshabilitado).")

    resumen = {}
    fallidos = []

    for centro in centros:
        slug = centro["slug"]
        if solo_centro and slug != solo_centro:
            continue
        maps_url = centro.get("maps_url")
        if not maps_url:
            log(f"- {slug}: sin maps_url, se omite (ej. CAPS Móvil)")
            continue

        log(f"=== {slug} ({centro.get('nombre')}) ===")
        items = None
        fuente = None

        place_id = place_id_de_brila(slug)
        if token and place_id:
            try:
                items = fetch_apify(slug, place_id, token)
                fuente = "apify"
            except Exception as e:
                log(f"  [apify] {slug}: FALLÓ — {e}")
        elif token and not place_id:
            log(f"  [apify] {slug}: no se pudo derivar el Place ID desde centros-brila-info/")

        if items is None and skey:
            cid = cid_de_maps_url(maps_url)
            if cid:
                try:
                    items = fetch_serper(slug, cid, skey)
                    fuente = "serper"
                except Exception as e:
                    log(f"  [serper] {slug}: FALLÓ — {e}")
            else:
                log(f"  [serper] {slug}: no se pudo derivar el cid desde maps_url")

        if items is None:
            log(f"  {slug}: AMBAS fuentes fallaron o no disponibles — se omite (sin inventar datos)")
            fallidos.append(slug)
            continue

        out_path = os.path.join(OUT_DIR, f"{slug}.json")
        payload = {
            "_meta": {
                "slug": slug,
                "nombre": centro.get("name"),
                "fuente": fuente,
                "place_id": place_id,
                "maps_url": maps_url,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "n": len(items),
            },
            "reviews_raw": items,
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        log(f"  guardado {out_path} ({len(items)} reseñas, fuente={fuente})")
        resumen[slug] = {"n": len(items), "fuente": fuente}

    log("\n=== RESUMEN ===")
    for slug, info in resumen.items():
        log(f"  {slug}: {info['n']} reseñas ({info['fuente']})")
    if fallidos:
        log(f"  FALLIDOS: {', '.join(fallidos)}")


if __name__ == "__main__":
    main()
