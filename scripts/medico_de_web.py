#!/usr/bin/env python3
"""Pone el médico del horario elegido en los turnos que el paciente sacó por la
web y quedaron sin médico.

Hasta 2026-10-24 la confirmación automática (`resuelta_por = 'auto:web'`)
aprobaba el turno sin guardar al médico, aunque el paciente había elegido el
horario de uno (`slot_doctor_id`): el turno aparecía en «Sin médico asignado».
El camino ya guarda el médico (`booking_create._auto_confirm_doctor`); este
script completa las filas FUTURAS escritas antes, con la MISMA regla: el médico
del horario sólo cuenta si está en el pool del servicio.

Seguro por construcción:
  * Por defecto NO escribe (dry-run): reporta qué filas cambiaría y cuáles
    quedan como están (sin médico en el horario, o fuera del pool).
  * --apply escribe en UNA transacción; cada UPDATE sólo pega si la fila sigue
    sin médico (otra escritura en el medio la deja intacta). Un segundo
    --apply no encuentra nada.
  * --json trae `copia`: el estado anterior de cada fila afectada (sin datos
    del paciente: ids, servicio, centro, horario y el médico del horario),
    para guardarla cifrada antes de --apply.
  * Imprime ids de solicitud, ids de médico y conteos — ningún dato del
    paciente.

Uso (desde el directorio de la app; en el contenedor, /app):
    python3 scripts/medico_de_web.py              # dry-run
    python3 scripts/medico_de_web.py --json       # dry-run + copia para guardar
    python3 scripts/medico_de_web.py --apply      # completa las filas
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


def _sin_medico():
    """The row has no doctor yet: no id and a NULL or blank name."""
    return sa.and_(bk.c.assigned_doctor_id.is_(None),
                   sa.func.coalesce(bk.c.assigned_doctor, sa.literal_column("''")) == sa.literal_column("''"))


def clasificar(filas, elegir):
    """Decisión pura sobre filas {id, service_id, slot_doctor_id, ...}:
    (completar, a_mano). `elegir(servicio_id, medico_id)` devuelve (nombre, id)
    o (None, None) — la regla de la confirmación automática."""
    completar, a_mano = [], []
    for f in filas:
        nombre, mid = elegir(f.get("service_id"), f.get("slot_doctor_id"))
        if nombre and mid:
            completar.append({"id": f["id"], "medico_id": mid, "nombre": nombre})
        else:
            a_mano.append({"id": f["id"], "medico_id": f.get("slot_doctor_id"),
                           "motivo": "medico_fuera_del_pool_o_del_catalogo"})
    return completar, a_mano


def _leer(db):
    with db._connect() as conn, conn.cursor() as cur:
        return sqlcore.run(cur, sa.select(
            bk.c.id, bk.c.service_id, bk.c.center_slug, bk.c.appointment_at, bk.c.slot_doctor_id,
            bk.c.assigned_doctor, bk.c.assigned_doctor_id
        ).where(
            bk.c.status == sa.literal_column("'aprobada'"), bk.c.resolved_by == sa.literal_column("'auto:web'"),
            bk.c.slot_doctor_id.is_not(None), _sin_medico(), bk.c.appointment_at >= sa.func.now()
        ).order_by(bk.c.id)).fetchall()


def _aplicar(db, cambios):
    """Todo en UNA transacción. Devuelve cuántas filas cambiaron."""
    n = 0
    with db._connect() as conn, conn.cursor() as cur:
        for c in cambios:
            n += sqlcore.run(cur, sa.update(bk).where(bk.c.id == c["id"], _sin_medico()).values(
                assigned_doctor=c["nombre"], assigned_doctor_id=c["medico_id"])).rowcount
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="completa las filas (si no, dry-run)")
    ap.add_argument("--json", action="store_true", help="salida JSON, con la copia de las filas afectadas")
    args = ap.parse_args(argv)

    from app.core import db
    from app.modules.turnos import booking_create
    filas = _leer(db)
    completar, a_mano = clasificar(filas, booking_create._auto_confirm_doctor)
    escritas = _aplicar(db, completar) if args.apply else 0

    if args.json:
        copia = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in f.items()} for f in filas]
        print(json.dumps({"aplicado": bool(args.apply), "revisadas": len(filas), "escritas": escritas,
                          "completar": [{"id": c["id"], "medico_id": c["medico_id"]} for c in completar],
                          "a_mano": a_mano, "copia": copia}, ensure_ascii=False, indent=2))
        return 0
    print(f"Turnos web futuros sin médico y con médico en el horario: {len(filas)}")
    print(f"  completar: {len(completar)}  " + " ".join(f"#{c['id']}→{c['medico_id']}" for c in completar))
    print(f"  a mano:    {len(a_mano)}  " + " ".join(f"#{c['id']}[{c['motivo']}]" for c in a_mano))
    if args.apply:
        print(f"\nAplicado: {escritas} filas completadas.")
    else:
        print("\n(dry-run: no se escribió nada. Con --apply se completan las filas.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
