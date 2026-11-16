"""The catalog an installation edited in its panel, written back as its data pack.

pinamar.humanrounds.org → salud.pinamar.gob.ar (the product owner, 2026-10-26): the
hospital team edited agendas, opening dates, services and doctors on the test
installation; those edits live in the database overlay (`catalog` table) on
top of the town pack. The municipal installation receives its catalog as a
pack (copied to its volume, see humanrounds-pinamar-data), so this script
writes a COPY of the active town pack with the overlay applied — exactly what
the live site shows:

  * an edited entry is the edited version; a panel-created one is added;
  * a disabled one (`borrado`) is left out; `--skip-id` leaves out more (tests);
  * no doctor email goes into the pack (staff accounts carry it,
    scripts/fixes/staff_transfer.py), other fields travel as they are;
  * the photos uploaded in the panel for live doctors and centers are copied
    next to the pack (`data/medico-fotos`, `data/centro-fotos`) — they live on
    the volume, not in a pack;
  * the pack's INVENTORY.json is rewritten for the new bytes, macOS `._*`
    sidecars are dropped.

What a pack cannot carry is named, not dropped silently: site settings in the
overlay (`config`: holidays, theme…) are listed in the report. A composable
file's overlay (conocimiento) would need tombstones this script does not
write: it refuses and writes nothing.

Reads only: the source pack and the database are never written. Repeatable:
the same database gives the same bytes; an existing output is replaced only
with `--force`, and only once the new one is complete. The output folder may
not hold, or lie inside, the app, an active pack or the uploaded photos —
`--force` removes what it replaces.

Run from the app directory (the container's /app):
    python3 scripts/fixes/bake_catalog_pack.py --out /tmp/catalogo --skip-id prueba-qa
    python3 scripts/fixes/bake_catalog_pack.py --out /tmp/catalogo --force     # again, same bytes
Prints counts only.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlalchemy as sa  # noqa: E402

from app.core import data_packs, db, sqlcore  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import schema_model as sm  # noqa: E402
from app.patient_web import catalog, doctor_photos  # noqa: E402

# Whole-file override types: the town pack supplies the entire current list.
BAKED = ("centros", "servicios", "medicos", "preparaciones", "enlaces")
# Fields that never go into a pack, per type.
STRIP = {"medicos": ("email",)}
PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp")
REPORT = "bake-report.json"


def _dump(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _overlay_types():
    """{type: rows} of every catalog type the overlay holds."""
    t = sm.catalog
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t.c.type, sa.func.count().label("n"))
                           .group_by(t.c.type).order_by(t.c.type)).fetchall()
    return {r["type"]: r["n"] for r in rows}


def _bake_type(tipo, skip):
    """(document to write, file name, stats) for one whole-file type."""
    cfg = catalog._TYPES[tipo]
    key = cfg["id_key"]
    extra = {"incluir_incompletos": True} if tipo == "medicos" else {}
    items, meta = catalog._merged(tipo, incluir_borrados=False, strict=True, **extra)
    rows = db_catalog.catalog_list(tipo)
    stats = {"en_pack": 0, "editados": 0, "nuevos": 0,
             "deshabilitados_fuera": sum(1 for r in rows if r.get("borrado")), "omitidos": 0}
    out = []
    for it in items:
        iid = str(it.get(key))
        if iid in skip:
            stats["omitidos"] += 1
            continue
        doc = {k: v for k, v in it.items() if k not in STRIP.get(tipo, ())}
        out.append(doc)
        origin = (meta.get(it.get(key)) or {}).get("_origen")
        if origin == "json+db":
            stats["editados"] += 1
        elif origin == "db":
            stats["nuevos"] += 1
    stats["en_pack"] = len(out)
    base = catalog._load_json(cfg["file"]) or {}
    document = dict(base) if isinstance(base, dict) else {}
    document[cfg["list_key"]] = out
    return document, cfg["file"], stats, {str(d.get(key)) for d in out}


def _photos(directory, ids):
    """[(path, name)] of the uploaded photos in `directory` whose stem is a live id.

    A processed doctor portrait is published as `<id>.webp`, a symlink into
    its private bundle under `.originals/` (doctor_photos._save_portrait): it
    travels as a plain file holding the portrait — the private original and
    its history stay on the source. A link that leads outside `directory`
    never travels."""
    if not os.path.isdir(directory):
        return []
    root = os.path.realpath(directory)
    found = []
    for name in sorted(os.listdir(directory)):
        stem, ext = os.path.splitext(name)
        path = os.path.join(directory, name)
        if ext.lower() not in PHOTO_EXTS or stem not in ids:
            continue
        target = os.path.realpath(path)
        if os.path.commonpath([target, root]) == root and os.path.isfile(target):
            found.append((target, name))
    return found


def _overlaps(a, b):
    """True when one real path is the other or lies inside it."""
    a, b = os.path.realpath(a), os.path.realpath(b)
    return os.path.commonpath([a, b]) in (a, b)


def _live_paths(pack_dir):
    """What the running installation reads: the source pack, every pack of the
    active chain, the uploaded photos and the app directory itself. The output
    may touch none of them — --force removes what it replaces."""
    live = [pack_dir, ROOT, doctor_photos.PHOTOS_DIR, doctor_photos.CENTER_PHOTOS_DIR]
    for pid in data_packs.active_chain() or []:
        live.append(data_packs.pack_dir(pid))
    return [p for p in live if p]


def _ignore(_dir, names):
    return [n for n in names if n.startswith("._") or n == ".DS_Store" or n == data_packs._INVENTORY_NAME]


def run(out, pack_dir=None, skip_ids=(), force=False, emit=print):
    if pack_dir is None:
        chain = data_packs.active_chain()
        if not chain:
            emit("ERROR: no hay cadena de packs activa — indicá --pack-dir")
            return 2
        pack_dir = data_packs.pack_dir(chain[-1])
    pack_dir = os.path.abspath(pack_dir)
    pack_id = os.path.basename(pack_dir)
    if not os.path.isfile(os.path.join(pack_dir, "pack.json")):
        emit("ERROR: %s no es un pack (falta pack.json)" % pack_id)
        return 2
    out = os.path.abspath(out)
    pack_out = os.path.join(out, "packs", pack_id)
    photo_dirs = {"medicos": doctor_photos.PHOTOS_DIR, "centros": doctor_photos.CENTER_PHOTOS_DIR}
    photo_outs = {t: os.path.join(out, "data", os.path.basename(d)) for t, d in photo_dirs.items()}
    written = [pack_out, os.path.join(out, REPORT)] + list(photo_outs.values())
    if any(_overlaps(out, live) for live in _live_paths(pack_dir)):
        emit("ERROR: la salida no puede estar dentro de la app, del pack activo ni de las fotos "
             "que sirve esta instalación — elegí una carpeta aparte (p. ej. /tmp/catalogo)")
        return 2
    if any(os.path.exists(p) for p in written) and not force:
        emit("ERROR: la salida ya existe — usá --force para reemplazarla")
        return 2

    overlay = _overlay_types()
    composable = sorted(t for t in overlay if t in catalog._TYPES and t not in BAKED)
    if composable:
        emit("ERROR: el overlay tiene filas de %s, que un pack no puede expresar sin "
             "lápidas — no se escribió nada" % ", ".join(composable))
        return 3

    skip = {str(i) for i in skip_ids}
    report = {"pack": pack_id, "fotos": 0, "fuera_del_pack": {}}
    files, live = {}, {}
    for tipo in BAKED:
        if not overlay.get(tipo):
            continue
        document, fname, stats, ids = _bake_type(tipo, skip)
        files[fname] = document
        live[tipo] = ids
        report[tipo] = stats
    for tipo in ("medicos", "centros"):
        if tipo not in live:
            key = catalog.id_key(tipo)
            extra = {"incluir_incompletos": True} if tipo == "medicos" else {}
            items, _ = catalog._merged(tipo, incluir_borrados=False, strict=True, **extra)
            live[tipo] = {str(it.get(key)) for it in items} - skip
    for tipo, n in overlay.items():
        if tipo in catalog._TYPES:
            continue
        if tipo == "config":
            for row in db_catalog.catalog_list("config"):
                report["fuera_del_pack"]["config/%s" % row["id"]] = 1
        else:
            report["fuera_del_pack"][tipo] = n

    # Everything is written into a staging folder next to the output first; the
    # previous output is replaced only once the new one is complete, path by path.
    os.makedirs(out, exist_ok=True)
    stage = tempfile.mkdtemp(prefix=".bake-", dir=out)
    try:
        staged = {p: os.path.join(stage, os.path.relpath(p, out)) for p in written}
        stage_pack = staged[pack_out]
        shutil.copytree(pack_dir, stage_pack, ignore=_ignore, symlinks=False)
        for fname, document in sorted(files.items()):
            _dump(os.path.join(stage_pack, fname), document)
        data_packs.write_inventory(stage_pack)
        for tipo, directory in photo_dirs.items():
            found = _photos(directory, live.get(tipo, set()))
            if found:
                os.makedirs(staged[photo_outs[tipo]], exist_ok=True)
            for path, name in found:
                shutil.copyfile(path, os.path.join(staged[photo_outs[tipo]], name))
                report["fotos"] += 1
        _dump(staged[os.path.join(out, REPORT)], report)
        for path in written:
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
            if os.path.exists(staged[path]):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                os.replace(staged[path], path)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    emit(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", required=True, help="output folder (packs/<id>, data/…, bake-report.json)")
    parser.add_argument("--pack-dir", help="the town pack to start from (default: the active chain's top pack)")
    parser.add_argument("--skip-id", action="append", default=[],
                        help="an entry id that must not travel (test doctors); repeatable")
    parser.add_argument("--force", action="store_true", help="replace an existing output")
    args = parser.parse_args(argv)
    return run(args.out, pack_dir=args.pack_dir, skip_ids=args.skip_id, force=args.force)


if __name__ == "__main__":
    sys.exit(main())
