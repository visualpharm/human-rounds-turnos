"""Solicitudes APROBADAS cuyo médico no atiende su servicio — sólo lectura.

Mapa del hospital (desk.json d10, problems.md №2, 2026-10-23): la mesa podía
aprobar con un médico de OTRA especialidad (el panel lo preelegía cuando el
servicio no tenía médicos de la suya, y el servidor aceptaba cualquier
nombre). Desde el arreglo la aprobación lo rechaza; este script lista las que
ya quedaron aprobadas así, para que una persona las revise y las corrija a
mano. No escribe nada: no tiene modo --apply.

La regla es la misma que verifica la aprobación
(`booking_requests._doctor_pool`, o sea `schedule_policy.belongs`): el médico
está en el pool del servicio. Se juzga por `medico_asignado_id` cuando la fila
lo tiene; si no, por el nombre, como la aprobación.

Correr desde el directorio de la app (en el contenedor, /app):
    python3 scripts/auditar_medico_servicio.py              # todas las aprobadas
    python3 scripts/auditar_medico_servicio.py --futuras    # sólo turnos por venir
    python3 scripts/auditar_medico_servicio.py --json       # salida para máquinas
Imprime ids de solicitud, fecha del turno, servicio y médico — ningún dato
del paciente.
"""
import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def auditar(filas, medicos, servicio_de):
    """Decisión pura, sin base: [{id, turno_dt, servicio_id, servicio,
    medico, medico_id, motivo}] de las filas aprobadas cuyo médico no está en
    el pool de su servicio. `servicio_de(id)` → registro completo o {}.
    Filas sin médico o de laboratorio (no llevan médico) no se juzgan; un
    servicio que ya no está en el catálogo se lista con su motivo propio."""
    from app.modules.turnos import booking_requests as br
    from app.modules.turnos import doctor_appointments as da
    vivos = [m for m in medicos if isinstance(m, dict) and not m.get("_borrado") and m.get("nombre")]
    por_id = {str(m.get("id")): m for m in vivos if m.get("id")}
    out = []
    for f in filas:
        nombre = str(f.get("medico_asignado") or "").strip()
        mid = str(f.get("medico_asignado_id") or "").strip()
        if not (nombre or mid):
            continue
        servicio = servicio_de(f.get("servicio_id")) or {}
        if servicio and br._is_lab(servicio):
            continue
        if not servicio:
            motivo = "servicio_desconocido"
        else:
            if mid and mid in por_id:
                candidatos = [por_id[mid]]
            else:
                exactos = [m for m in vivos if da._norm(m["nombre"]) == da._norm(nombre)]
                candidatos = exactos or [m for m in vivos if da._name_matches(nombre, m["nombre"])]
            if not candidatos:
                motivo = "medico_fuera_del_catalogo"
            elif br._doctor_pool(servicio, candidatos):
                continue
            else:
                motivo = "medico_no_atiende_servicio"
        turno = f.get("turno_dt")
        if isinstance(turno, datetime):  # siempre en la hora de la instalación
            from app.core import tz
            turno = turno.astimezone(tz.tzinfo()).isoformat(timespec="minutes")
        out.append({
            "id": f.get("id"),
            "turno_dt": turno,
            "servicio_id": f.get("servicio_id"),
            "servicio": servicio.get("nombre") or f.get("servicio_id"),
            "medico": nombre, "medico_id": mid or None, "motivo": motivo,
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--futuras", action="store_true", help="sólo turnos que todavía no pasaron")
    ap.add_argument("--json", action="store_true", help="salida JSON")
    args = ap.parse_args(argv)

    from app.core import tz
    from app.core import db_booking
    from app.patient_web import catalog
    from app.modules.turnos import booking_requests as br

    filas = db_booking.list_admin("aprobada", limit=1_000_000)
    if args.futuras:
        ahora = datetime.now(tz.tzinfo())
        filas = [f for f in filas if isinstance(f.get("turno_dt"), datetime) and f["turno_dt"] >= ahora]
    hallazgos = auditar(filas, catalog.doctors() or [], br._service_full)
    if args.json:
        print(json.dumps({"aprobadas": len(filas), "hallazgos": hallazgos},
                         ensure_ascii=False, indent=2, default=str))
        return 0
    print(f"Aprobadas revisadas: {len(filas)} · con médico que no atiende su servicio: {len(hallazgos)}")
    for h in hallazgos:
        print(f"  #{h['id']}  {h['turno_dt'] or '—'}  {h['servicio']}  ←  {h['medico'] or h['medico_id']}"
              f"  [{h['motivo']}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
