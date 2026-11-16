#!/usr/bin/env python3
"""Empareja el médico guardado de cada solicitud con el catálogo (nombre + id).

Desde 2026-10-23 (problems.md №3) el médico de una solicitud es UN par: la
fuente de verdad es `assigned_doctor_id` y `assigned_doctor` es el nombre del
catálogo para ese id (`doctor_appointments.resolve_pair` en toda escritura).
Las filas escritas antes pueden estar desparejas; este script las clasifica
en tres grupos con la MISMA regla que la escritura:

  1. completar_id    — nombre sin id que resuelve a UN médico del catálogo:
                       se completa el id (y el nombre pasa a ser el del catálogo);
                       sin ninguno vivo, UN médico deshabilitado también cuenta.
  2. corregir_nombre — id del catálogo con un nombre guardado distinto: se
                       pone el nombre del catálogo (el id manda).
  3. a_mano          — nombre ambiguo o sin match, o un id que el catálogo ya
                       no tiene: sólo se reporta, lo resuelve una persona.

Por qué no es una migración de Alembic: necesita el catálogo (packs JSON +
overlay de la base), que Alembic no ve.

Seguro por construcción:
  * Por defecto NO escribe (dry-run): reporta los tres grupos.
  * --apply ejecuta 1 y 2 en UNA transacción; cada UPDATE sólo pega si la
    fila sigue como se leyó (otra escritura en el medio la deja intacta).
  * Imprime ids de solicitud, ids de médico, motivos y conteos — ningún
    dato del paciente ni nombres.

Uso (desde el directorio de la app; en el contenedor, /app):
    python3 scripts/medico_por_id.py              # dry-run
    python3 scripts/medico_por_id.py --apply      # corrige grupos 1 y 2
    python3 scripts/medico_por_id.py --json       # salida para máquinas
"""

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlalchemy as sa  # noqa: E402

from app.core import sqlcore  # noqa: E402
from app.core.schema_model import booking_requests as bk  # noqa: E402


def clasificar(filas):
    """Decisión pura sobre filas {id, assigned_doctor, assigned_doctor_id}:
    (completar_id, corregir_nombre, a_mano). Las dos primeras llevan el par
    nuevo (`nombre`, `medico_id`) y el viejo para el UPDATE condicional."""
    from app.modules.turnos import doctor_appointments as da
    try:
        from app.patient_web import catalog
        medicos = [m for m in (catalog.doctors() or []) if isinstance(m, dict) and m.get("nombre")]
        vivos = {m.get("id") for m in (catalog.doctors(incluir_incompletos=True) or [])
                 if isinstance(m, dict)}
        deshabilitados = [m for m in (catalog.doctors(incluir_incompletos=True,
                                                      incluir_deshabilitados=True) or [])
                          if isinstance(m, dict) and m.get("nombre") and m.get("id") not in vivos]
    except Exception:  # noqa: BLE001
        medicos, deshabilitados = [], []
    completar, corregir, a_mano = [], [], []
    for f in filas:
        nombre = (f.get("assigned_doctor") or "").strip()
        mid = (f.get("assigned_doctor_id") or "").strip()
        if not (nombre or mid):
            continue
        viejo = {"viejo_nombre": f.get("assigned_doctor"), "viejo_id": f.get("assigned_doctor_id")}
        if mid:
            medico = da.doctor_by_id(mid)  # deshabilitados incluidos (2bd548d2)
            if not (medico and medico.get("nombre")):
                a_mano.append({"id": f["id"], "medico_id": mid, "motivo": "id_fuera_del_catalogo"})
            elif nombre != str(medico["nombre"]):
                corregir.append({"id": f["id"], "medico_id": mid,
                                 "nombre": str(medico["nombre"]), **viejo})
            continue
        nombre_cat, resuelto = da.resolve_pair(nombre)
        if resuelto:
            completar.append({"id": f["id"], "medico_id": resuelto, "nombre": nombre_cat, **viejo})
            continue
        candidatos = [m for m in medicos if da._name_matches(nombre, m["nombre"])]
        if not candidatos:
            # Una fila vieja de un médico deshabilitado después (2026-10-24):
            # el nombre no resuelve en el catálogo vivo, pero la solicitud es
            # suya — se completa sólo si UN deshabilitado la nombra (exacto
            # gana entre varios), igual que resolve_doctor_id con los vivos.
            previos = [m for m in deshabilitados if da._name_matches(nombre, m["nombre"])]
            exactos = [m for m in previos if da._norm(m["nombre"]) == da._norm(nombre)]
            unico = previos if len(previos) == 1 else exactos if len(exactos) == 1 else []
            if unico:
                completar.append({"id": f["id"], "medico_id": str(unico[0]["id"]),
                                  "nombre": str(unico[0]["nombre"]), **viejo})
                continue
        a_mano.append({"id": f["id"], "medico_id": None,
                       "motivo": "nombre_ambiguo" if len(candidatos) > 1 else "nombre_sin_match"})
    return completar, corregir, a_mano


def _leer(db):
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(bk.c.id, bk.c.assigned_doctor, bk.c.assigned_doctor_id).where(
            sa.or_(bk.c.assigned_doctor.is_not(None), bk.c.assigned_doctor_id.is_not(None))
        ).order_by(bk.c.id)).fetchall()


def _aplicar(db, cambios):
    """Grupos 1 y 2 en UNA transacción. Devuelve cuántas filas cambiaron."""
    n = 0
    with db._connect() as conn, conn.cursor() as cur:
        for c in cambios:
            n += sqlcore.run(cur, sa.update(bk).where(
                bk.c.id == c["id"],
                bk.c.assigned_doctor.is_not_distinct_from(c["viejo_nombre"]),
                bk.c.assigned_doctor_id.is_not_distinct_from(c["viejo_id"]),
            ).values(assigned_doctor=c["nombre"], assigned_doctor_id=c["medico_id"])).rowcount
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="corrige los grupos 1 y 2 (si no, dry-run)")
    ap.add_argument("--json", action="store_true", help="salida JSON")
    args = ap.parse_args(argv)

    from app.core import db
    filas = _leer(db)
    completar, corregir, a_mano = clasificar(filas)
    escritas = _aplicar(db, completar + corregir) if args.apply else 0

    def publico(lista):
        return [{k: v for k, v in c.items() if k in ("id", "medico_id", "motivo")} for c in lista]

    out = {"aplicado": bool(args.apply), "revisadas": len(filas), "escritas": escritas,
           "completar_id": publico(completar), "corregir_nombre": publico(corregir),
           "a_mano": publico(a_mano)}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"Solicitudes con médico: {len(filas)}")
    print(f"  1. completar id:     {len(completar)}  " + " ".join(f"#{c['id']}→{c['medico_id']}" for c in completar))
    print(f"  2. corregir nombre:  {len(corregir)}  " + " ".join(f"#{c['id']}({c['medico_id']})" for c in corregir))
    print(f"  3. a mano:           {len(a_mano)}  " + " ".join(f"#{c['id']}[{c['motivo']}]" for c in a_mano))
    if args.apply:
        print(f"\nAplicado: {escritas} filas corregidas (grupos 1 y 2). El grupo 3 queda para revisar a mano.")
    else:
        print("\n(dry-run: no se escribió nada. Con --apply se corrigen los grupos 1 y 2.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
