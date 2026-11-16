"""What Alembic 0030_date_time_columns will change — read-only.

`slot_holds.day/time`, `lab_report_analysis.fecha` and `patient_lab_uploads.fecha`
were text. The revision turns them into `date` / `time` and never fails on a bad
value: an impossible `fecha` becomes NULL, an impossible or duplicated hold leaves
`slot_holds` — each first copied whole into `date_time_conversion_leftovers`, so
nothing is lost. This lists, before upgrading, exactly which rows that is — ids
and the date/time text only, nothing that identifies a person. Opens the
database read-only; nothing is written.

    python3 scripts/fixes/report_fechas_texto.py [--json]

A database that is already converted reports zero everywhere.
"""
import json
import os
import re
import sys
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlalchemy as sa  # noqa: E402

from app.core import db, schema_model, sqlcore  # noqa: E402
from app.core.schema_model import information_schema_columns as isc, slot_holds  # noqa: E402

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")
_FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _day(value):
    value = str(value or "").strip()
    if not _DAY_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _time(value):
    value = str(value or "").strip()
    if not _TIME_RE.match(value):
        return None
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError:
        return None


def _fecha(value):
    value = str(value or "").strip()
    if not _FECHA_RE.match(value):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _type(cur, table, column):
    row = sqlcore.run(cur, sa.select(isc.c.data_type).where(
        isc.c.table_schema == sa.func.current_schema(),
        isc.c.table_name == table, isc.c.column_name == column)).fetchone()
    if not row:
        return None
    return row["data_type"] if isinstance(row, dict) else row[0]


def _fecha_report(cur, table):
    tipo = _type(cur, table, "fecha")
    out = {"tabla": table, "tipo": tipo, "filas": 0, "con_fecha": 0, "a_normalizar": [], "a_vaciar": []}
    if tipo is None:
        return out
    t = schema_model.metadata.tables[table]
    out["filas"] = _scalar(sqlcore.run(cur, sa.select(sa.func.count().label("n")).select_from(t)).fetchone())
    if tipo != "text":
        return out
    rows = sqlcore.run(cur, sa.select(t.c.id, t.c.fecha).where(t.c.fecha.is_not(None)).order_by(t.c.id)).fetchall()
    for row in rows:
        id_, fecha = (row["id"], row["fecha"]) if isinstance(row, dict) else row
        out["con_fecha"] += 1
        fixed = _fecha(fecha)
        if fixed is None:
            out["a_vaciar"].append({"id": id_, "valor": fecha})
        elif fixed.isoformat() != fecha:
            out["a_normalizar"].append({"id": id_, "valor": fecha, "queda": fixed.isoformat()})
    return out


def _scalar(row):
    return row["n"] if isinstance(row, dict) else row[0]


def _holds_report(cur):
    dia, hora = _type(cur, "slot_holds", "day"), _type(cur, "slot_holds", "time")
    out = {"tabla": "slot_holds", "tipo_day": dia, "tipo_time": hora, "filas": 0,
           "a_borrar_imposibles": [], "a_borrar_duplicados": []}
    if dia is None:
        return out
    out["filas"] = _scalar(sqlcore.run(cur, sa.select(sa.func.count().label("n")).select_from(slot_holds)).fetchone())
    if dia != "text" and hora != "text":
        return out
    rows = sqlcore.run(cur, sa.select(
        slot_holds.c.id, slot_holds.c.service_id, slot_holds.c.day, slot_holds.c.time,
        slot_holds.c.request_id).order_by(slot_holds.c.id)).fetchall()
    groups = {}
    for row in rows:
        id_, service_id, day, hour, request_id = (
            (row["id"], row["service_id"], row["day"], row["time"], row["request_id"])
            if isinstance(row, dict) else row)
        key = (service_id, _day(day), _time(hour))
        if key[1] is None or key[2] is None:
            out["a_borrar_imposibles"].append(
                {"id": id_, "servicio": service_id, "day": str(day), "time": str(hour),
                 "con_solicitud": request_id is not None})
            continue
        groups.setdefault(key, []).append((request_id is not None, id_))
    for key, members in groups.items():
        if len(members) > 1:
            members.sort()
            kept = members.pop()
            out["a_borrar_duplicados"].append(
                {"servicio": key[0], "day": key[1].isoformat(), "time": key[2].strftime("%H:%M"),
                 "se_queda": kept[1], "se_borran": [m[1] for m in members]})
    return out


def report(conn):
    """{holds, lab_report_analysis, patient_lab_uploads, total_afectado} — the rows
    0030 would change or set aside; zeros when the columns are already typed."""
    with conn.cursor() as cur:
        out = {"holds": _holds_report(cur),
               "lab_report_analysis": _fecha_report(cur, "lab_report_analysis"),
               "patient_lab_uploads": _fecha_report(cur, "patient_lab_uploads")}
    holds = out["holds"]
    out["total_afectado"] = (
        len(holds["a_borrar_imposibles"])
        + sum(len(d["se_borran"]) for d in holds["a_borrar_duplicados"])
        + sum(len(out[t]["a_normalizar"]) + len(out[t]["a_vaciar"])
              for t in ("lab_report_analysis", "patient_lab_uploads")))
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    import psycopg
    from psycopg.rows import dict_row
    url = db._db_url()
    if not url:
        print("DATABASE_URL no configurada", file=sys.stderr)
        return 2
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=5) as conn:
        conn.read_only = True
        r = report(conn)
    if "--json" in argv:
        print(json.dumps(r, default=str, ensure_ascii=False, indent=2))
        return 0
    h = r["holds"]
    print(f"slot_holds: {h['filas']} filas, day={h['tipo_day']}, time={h['tipo_time']}")
    print(f"  imposibles (saldrían de la tabla, copiados enteros a date_time_conversion_leftovers): "
          f"{len(h['a_borrar_imposibles'])}")
    for d in h["a_borrar_imposibles"]:
        print(f"    id {d['id']}: {d['servicio']} day={d['day']!r} time={d['time']!r} "
              f"{'ligado a una solicitud' if d['con_solicitud'] else 'suelto'}")
    print(f"  mismo slot repetido (la copia vieja sale de la tabla, copiada a leftovers): "
          f"{len(h['a_borrar_duplicados'])} slots")
    for d in h["a_borrar_duplicados"]:
        print(f"    {d['servicio']} {d['day']} {d['time']}: queda {d['se_queda']}, salen {d['se_borran']}")
    for tabla in ("lab_report_analysis", "patient_lab_uploads"):
        t = r[tabla]
        print(f"{tabla}: {t['filas']} filas, fecha={t['tipo']}, con fecha {t['con_fecha']}")
        print(f"  se normalizan (día ISO con hora): {len(t['a_normalizar'])}")
        for d in t["a_normalizar"]:
            print(f"    id {d['id']}: {d['valor']!r} -> {d['queda']}")
        print(f"  se vacían a NULL (no es un día real; el valor se copia a leftovers): {len(t['a_vaciar'])}")
        for d in t["a_vaciar"]:
            print(f"    id {d['id']}: {d['valor']!r}")
    print(f"TOTAL de filas que la revisión tocaría: {r['total_afectado']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
