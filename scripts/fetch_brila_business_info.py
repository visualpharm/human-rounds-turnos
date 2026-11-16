#!/usr/bin/env python3
"""Fetch business info for municipal health centers via Brila's open business-info endpoint.

No auth required (GET /api/v1/business-info on the staged SiteGen API — see
<workspace>/brila/SPEC.md section 9). Converts each centro's `?cid=` Google Maps
URL into the FID-style URL the endpoint expects, then stores one JSON file per
center under the configured private town pack.
"""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core import data_packs  # noqa: E402

PACK_DIR = Path(data_packs.private_pack_dir("ar-b-pinamar"))
CENTROS_PATH = PACK_DIR / "centros-salud.json"
OUT_DIR = PACK_DIR / "centros-brila-info"
API_URL = "https://sitegen.api-stage.brila.ai/api/v1/business-info"

SKIP_SLUGS = {"caps-movil"}


def cid_to_maps_url(cid_url):
    cid = urllib.parse.parse_qs(urllib.parse.urlparse(cid_url).query).get("cid", [None])[0]
    if not cid:
        return None
    fid = f"0x0:0x{int(cid):x}"
    return f"https://www.google.com/maps/place/data=!4m5!3m4!1s{fid}!8m2!3d0!4d0"


def fetch(maps_url):
    url = API_URL + "?" + urllib.parse.urlencode({"maps_url": maps_url})
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "curl/8.7.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main():
    centros = json.loads(CENTROS_PATH.read_text(encoding="utf-8"))["centros"]
    OUT_DIR.mkdir(exist_ok=True)

    for centro in centros:
        slug = centro["slug"]
        if slug in SKIP_SLUGS:
            print(f"{slug}: skipped")
            continue
        cid_url = centro.get("maps_url")
        if not cid_url:
            print(f"{slug}: no maps_url, skipped")
            continue
        maps_url = cid_to_maps_url(cid_url)
        if not maps_url:
            print(f"{slug}: could not derive FID url, skipped")
            continue
        try:
            info = fetch(maps_url)
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            print(f"{slug}: HTTP {error.code} {raw}", file=sys.stderr)
            continue
        out_path = OUT_DIR / f"{slug}.json"
        out_path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{slug}: saved ({info.get('name')}, rating={info.get('rating')}, reviews={info.get('review_count')})")


if __name__ == "__main__":
    main()
