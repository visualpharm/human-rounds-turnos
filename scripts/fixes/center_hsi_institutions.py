"""Give each center its HSI institution number from a file.

A center's `hsi_institution` lives in its catalog doc (2026-10-27): in the data
pack, or in the panel's overlay row once the center was edited in the panel —
and an overlay row replaces the pack's doc whole, so a number added to the pack
never reaches a center the team already edited. This script writes the number
into the overlay row of each such center and leaves the rest of its doc as it
is. A center the panel never edited is the pack's job: it is listed as
`falta_en_el_pack` and nothing is written for it, unless `--pack-items` asks
to give it an overlay row too (from then on the panel's copy wins over the
pack for that center).

The file is a JSON object, center slug -> number ({"hospital-x": "1086"}); a
center not in the file is not touched. Dry run by default: it prints one line
per center (`escribir` / `ya_esta` / `falta_en_el_pack` / `no_existe` /
`numero_invalido`) and the totals. `--apply` writes. Safe to run twice. It
prints slugs and numbers only.

Run from the app directory (the container's /app), dry run first:
    python3 scripts/fixes/center_hsi_institutions.py numbers.json
    python3 scripts/fixes/center_hsi_institutions.py numbers.json --apply
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db_catalog  # noqa: E402
from app.patient_web import catalog  # noqa: E402


def plan(numbers, centers, overlay_ids, pack_items=False):
    """Pure decision: [(slug, number, action)] — no database access.
    `centers` is every center the catalog knows (hidden ones included) by
    slug; `overlay_ids` the slugs with a panel overlay row."""
    out = []
    for slug, number in sorted(numbers.items()):
        number = "".join(str(number or "").split())
        center = centers.get(slug)
        if not number.isdigit():
            action = "numero_invalido"
        elif center is None:
            action = "no_existe"
        elif str(center.get("hsi_institution") or "") == number:
            action = "ya_esta"
        elif slug in overlay_ids or pack_items:
            action = "escribir"
        else:
            action = "falta_en_el_pack"
        out.append((slug, number, action))
    return out


def run(path, apply=False, pack_items=False, emit=print):
    with open(path, encoding="utf-8") as f:
        numbers = json.load(f)
    if not isinstance(numbers, dict):
        raise SystemExit("the file must be a JSON object: center slug -> number")
    centers = {c.get("slug"): c for c in catalog.admin_list("centros") if c.get("slug")}
    rows = {r["id"]: r for r in db_catalog.catalog_list("centros") or []}
    decision = plan(numbers, centers, set(rows), pack_items=pack_items)
    totals = {}
    for slug, number, action in decision:
        emit(f"{action:16} {slug} {number}")
        totals[action] = totals.get(action, 0) + 1
        if apply and action == "escribir":
            row = rows.get(slug)
            base = dict(row["doc"] or {}) if row else {
                k: v for k, v in centers[slug].items() if not k.startswith("_")}
            base["hsi_institution"] = number
            doc, err = catalog.validate("centros", base, is_new=False)
            if err:
                raise SystemExit(f"{slug}: {err}")
            db_catalog.catalog_upsert("centros", slug, doc, borrado=bool(row and row.get("borrado")))
    if apply:
        catalog.invalidate()
    emit(("aplicado " if apply else "simulacion ")
         + " ".join(f"{k}={v}" for k, v in sorted(totals.items())))
    return decision


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("file")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--pack-items", action="store_true",
                    help="also give a center the panel never edited its own overlay row")
    args = ap.parse_args(argv)
    run(args.file, apply=args.apply, pack_items=args.pack_items)


if __name__ == "__main__":
    main()
