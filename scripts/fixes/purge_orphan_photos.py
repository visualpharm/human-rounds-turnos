"""Remove the photos of doctors and centers that are in no catalog record.

Until 2026-11-06 a doctor or center deleted for good left its photos on disk:
the public `data/medico-fotos/<id>.<ext>` (a portrait pointer or a legacy
upload), the doctor's private `data/medico-fotos/.originals/<id>/` (originals,
bundles, metadata) and `data/centro-fotos/<slug>.<ext>`. Ids are derived from
names, so the next record with the same name showed the previous one's photo,
and the private original outlived its person (Ley 25.326). Deletion now takes
the photos along (`doctor_photos.erase`); this script removes what was left
before. The product owner's hub, 2026-11-06: «dry run by default, --apply only removes files
whose id is in no catalog row, active or disabled, and prints what it removed».

A photo is an orphan when its id is neither in the merged catalog (the data
pack and the panel's overlay, disabled records included) nor in any overlay
row of its type. A name that is not a valid id (the uploads' own pattern), a
file that is not a photo and anything outside the two folders are never
touched. The catalog is read again right before each removal (for a doctor,
under the same portrait lock the uploads publish under): a record created in
the meantime keeps its photo. A catalog that lists no doctor or no center is
refused (exit 2): every photo would look like an orphan.

Dry run by default — it only prints what it would remove. `--apply` removes
and prints each removal. Safe to run twice. It prints ids, never file contents.

Run from the app directory (the container's /app), dry run first:
    python3 scripts/fixes/purge_orphan_photos.py            # dry run
    python3 scripts/fixes/purge_orphan_photos.py --apply    # remove
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
from app.patient_web import doctor_photos  # noqa: E402

# catalog type -> doctor_photos entity
_ENTITIES = {"medicos": "doctors", "centros": "centers"}


def _known_ids(tipo):
    """Every id of `tipo` the catalog knows — disabled and still-incomplete
    records included (`catalog.known_ids`, strict: a catalog that cannot be read
    raises) — plus every overlay row of that type. Read fresh."""
    catalog.invalidate()
    ids = set(catalog.known_ids(tipo))
    ids |= {str(row.get("id")) for row in db_catalog.catalog_list(tipo) if row.get("id")}
    return ids


def _stored(ent):
    """(ids with a photo on disk, number of names ignored) for entity `ent`."""
    folder = doctor_photos._dir(ent)
    ids, ignored = set(), 0
    if not os.path.isdir(folder):
        return ids, ignored
    for name in os.listdir(folder):
        if name.startswith("."):
            continue  # .originals is read below; temporary and hidden files are not photos
        stem, _dot, ext = name.rpartition(".")
        if (ext in doctor_photos._EXTS and doctor_photos._SAFE_ID_RE.fullmatch(stem)
                and not os.path.isdir(os.path.join(folder, name))):
            ids.add(stem)
        else:
            ignored += 1
    originals = os.path.join(folder, ".originals")
    if ent == "doctors" and os.path.isdir(originals):
        for name in os.listdir(originals):
            path = os.path.join(originals, name)
            if doctor_photos._SAFE_ID_RE.fullmatch(name) and os.path.isdir(path) and not os.path.islink(path):
                ids.add(name)
            else:
                ignored += 1
    return ids, ignored


def run(apply=False, emit=print):
    report = {"modo": "aplicado" if apply else "simulacion", "huerfanas": {}, "quitadas": {},
              "ignoradas": 0}
    plan = {}
    for tipo, ent in _ENTITIES.items():
        known = _known_ids(tipo)
        if not known:
            emit(f"el catálogo no lista ningún registro de {tipo}: no se quita nada")
            raise SystemExit(2)
        stored, ignored = _stored(ent)
        report["ignoradas"] += ignored
        plan[tipo] = sorted(stored - known)
        report["huerfanas"][tipo] = list(plan[tipo])
        report["quitadas"][tipo] = []
    for tipo, ent in _ENTITIES.items():
        for id_ in plan[tipo]:
            if not apply:
                emit(f"huérfana  {tipo} {id_}")
                continue
            reappeared = []

            def in_catalog(id_=id_, tipo=tipo):
                reappeared.append(id_ in _known_ids(tipo))
                return reappeared[-1]

            removed = doctor_photos.erase(id_, ent, unless=in_catalog)
            if reappeared and reappeared[-1]:
                emit(f"conservada {tipo} {id_} (volvió al catálogo)")
            else:
                report["quitadas"][tipo].append(id_)
                emit(f"quitada   {tipo} {id_} ({removed})")
    emit(json.dumps(report, ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="remove (default: dry run)")
    args = parser.parse_args(argv)
    run(apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
