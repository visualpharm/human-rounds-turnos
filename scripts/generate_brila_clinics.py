#!/usr/bin/env python3
"""Start Brila websites for Sacoturno clinics that do not have one yet."""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.core import data_packs  # noqa: E402

PACK_DIR = Path(data_packs.private_pack_dir("ar-b-pinamar"))
CLINICS_PATH = PACK_DIR / "clinicas.json"
RESULTS_PATH = PACK_DIR / "brila-generation-results.json"
MAPS_URLS_PATH = PACK_DIR / "clinic-maps-urls.json"
API_BASE = "https://api.brila.ai/api/frontend"

_maps_url_cache = None


def _load_maps_url_cache():
    global _maps_url_cache
    if _maps_url_cache is None and MAPS_URLS_PATH.exists():
        import json as _json
        data = _json.loads(MAPS_URLS_PATH.read_text(encoding="utf-8"))
        _maps_url_cache = {
            slug: entry["maps_url"]
            for slug, entry in data.items()
            if entry.get("maps_url") and not entry.get("skip")
        }
    return _maps_url_cache or {}


def api_request(path, token, method="GET", query=None, body=None):
    url = API_BASE + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Sacoturno Brila batch/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def maps_url(slug, name):
    cache = _load_maps_url_cache()
    if slug in cache:
        return cache[slug]
    # Fallback: construct a search URL (may be rejected by Brila — prefer the cache)
    query = f"{name}, Pinamar, Buenos Aires, Argentina"
    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(query)


def clinic_targets():
    sys.path.insert(0, str(ROOT))
    from app.patient_web import availability_directory  # pylint: disable=import-error,import-outside-toplevel

    stored = json.loads(CLINICS_PATH.read_text(encoding="utf-8"))
    existing = {
        slug for slug, clinic in stored.items()
        if clinic.get("brila_url") or clinic.get("alias_of")
    }
    cache = _load_maps_url_cache()
    clinics = [c for c in availability_directory.all_clinicas() if c["slug"] not in existing]
    # Only include clinics that have a confirmed maps.app.goo.gl URL
    return [c for c in clinics if c["slug"] in cache]


def check_site_status(site_id, token):
    """Return latest operation status for a pending site."""
    try:
        ops = api_request(f"/v1/sites/{site_id}/operations", token, query={"page": 1, "per_page": 1})
        latest = (ops.get("site_operations") or [{}])[0].get("message", {})
        return latest.get("status"), latest.get("message", "")
    except urllib.error.HTTPError:
        return None, ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Start generation instead of previewing targets")
    parser.add_argument("--status", action="store_true", help="Check status of pending sites in results file")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of clinics to start")
    parser.add_argument("--delay", type=float, default=1.5, help="Seconds between Brila requests")
    args = parser.parse_args()

    if args.status:
        token = os.environ.get("BRILA_ACCESS_TOKEN", "").strip()
        if not token:
            raise SystemExit("BRILA_ACCESS_TOKEN is required for --status")
        results = json.loads(RESULTS_PATH.read_text(encoding="utf-8")) if RESULTS_PATH.exists() else {}
        for slug, data in results.items():
            site_id = data.get("id")
            if not site_id:
                continue
            status, msg = check_site_status(site_id, token)
            site_url = data.get("site_url", "")
            print(f"{slug}: {status} | {site_url or msg}")
        return

    targets = clinic_targets()
    if args.limit:
        targets = targets[:args.limit]
    if not args.execute:
        for clinic in targets:
            print(f'{clinic["slug"]}\t{maps_url(clinic["slug"], clinic["name"])}')
        print(f"{len(targets)} clinics ready for Brila generation", file=sys.stderr)
        return

    token = os.environ.get("BRILA_ACCESS_TOKEN", "").strip()
    if not token:
        raise SystemExit("BRILA_ACCESS_TOKEN is required for --execute")

    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8")) if RESULTS_PATH.exists() else {}
    for index, clinic in enumerate(targets, start=1):
        slug = clinic["slug"]
        source_url = maps_url(slug, clinic["name"])
        if results.get(slug, {}).get("id"):
            print(f"[{index}/{len(targets)}] {clinic['name']}: already started")
            continue
        try:
            business = api_request("/v1/sites/info", token, query={"maps_url": source_url})
            generated = api_request("/v1/sites", token, method="POST", query={"maps_url": source_url})
            results[slug] = {
                "name": clinic["name"],
                "maps_url": source_url,
                "business": business,
                **generated,
            }
            RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[{index}/{len(targets)}] {clinic['name']}: started")
        except urllib.error.HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw.startswith("{") else {}
            if parsed.get("type") == "GENERATION_IN_PROGRESS":
                # Already running — store the existing site_id so we don't re-submit
                existing_id = parsed.get("details", {}).get("site_id")
                results[slug] = {"name": clinic["name"], "maps_url": source_url, "id": existing_id}
                RESULTS_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print(f"[{index}/{len(targets)}] {clinic['name']}: already in progress (id={existing_id})")
            else:
                print(f"[{index}/{len(targets)}] {clinic['name']}: HTTP {error.code} {raw}", file=sys.stderr)
        time.sleep(args.delay)


if __name__ == "__main__":
    main()
