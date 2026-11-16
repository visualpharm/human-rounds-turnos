"""What Alembic 0027_consulta_reemplazada's backfill will touch — read-only.

Before 2026-10-23 a second «Confirmar» on the same interview created another
consultation with its own valid prescription. 0027 keeps the NEWEST of each
interview as the current one and only MARKS the older ones as superseded by it
(`superseded_at` / `superseded_by`): nothing is deleted and no prescription or
order text, token or link is rewritten — their /r/ /o/ links keep showing what
was issued, marked «ANULADA — REEMPLAZADA» with a link to the new one. Run this
before upgrading an installation to see which prescriptions that marks. It
opens a read-only transaction and never writes; the report carries no names,
no DNIs and no tokens (ids and dates only).

Run from the app directory (the container's /app):
    python3 scripts/fixes/report_consultas_duplicadas.py            # summary + one line per interview
    python3 scripts/fixes/report_consultas_duplicadas.py --json     # the same, machine-readable
Per interview it lists the consultation that stays current, the ones that get
marked, their creation times and whether the older prescription/order text is
identical to the current one (a double click) or different (a re-issue).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlalchemy as sa  # noqa: E402

from app.core import db, sqlcore  # noqa: E402
from app.core.schema_model import consultations, information_schema_columns as isc  # noqa: E402


def _summary(migrada):
    """Interviews with more than one consultation (only the still-current ones once 0027 is applied)."""
    c = consultations
    inner = sa.select(c.c.interview_id, sa.func.count().label("n"),
                      sa.func.min(c.c.created).label("primera"), sa.func.max(c.c.created).label("ultima"))
    if migrada:
        inner = inner.where(c.c.superseded_at.is_(None))
    d = inner.group_by(c.c.interview_id).having(sa.func.count() > sa.literal_column("1")).subquery("d")
    return sa.select(sa.func.count().label("entrevistas"),
                     sa.func.coalesce(sa.func.sum(d.c.n - sa.literal_column("1")), sa.literal_column("0")).label("a_marcar"),
                     sa.func.min(d.c.primera).label("desde"), sa.func.max(d.c.ultima).label("hasta")
                     ).select_from(d)


def _detail(migrada):
    """Each older consultation of those interviews, against the newest one (`vigente_id`)."""
    c = consultations.alias("c")
    newest = sa.select(consultations.c.interview_id, consultations.c.id, consultations.c.receta_json,
                       consultations.c.orden_json).distinct(consultations.c.interview_id)
    if migrada:
        newest = newest.where(consultations.c.superseded_at.is_(None))
    n = newest.order_by(consultations.c.interview_id, consultations.c.id.desc()).subquery("n")
    stmt = sa.select(
        c.c.interview_id, c.c.id, c.c.created, c.c.doctor_id,
        sa.and_(c.c.receta_json.is_not_distinct_from(n.c.receta_json),
                c.c.orden_json.is_not_distinct_from(n.c.orden_json)).label("mismo_contenido"),
        n.c.id.label("vigente_id")
    ).select_from(c.join(n, n.c.interview_id == c.c.interview_id)).where(c.c.id < n.c.id)
    if migrada:
        stmt = stmt.where(c.c.superseded_at.is_(None))
    return stmt.order_by(c.c.interview_id, c.c.id)


def report(conn):
    """{entrevistas, a_marcar, desde, hasta, ya_migrada, detalle} — a database
    already at 0027 only counts the duplicates still current (normally zero).
    `a_anular` is kept as an alias of `a_marcar`."""
    with conn.cursor() as cur:
        migrada = sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(isc).where(
            isc.c.table_name == sa.literal_column("'consultations'"),
            isc.c.column_name == sa.literal_column("'superseded_at'"))).fetchone() is not None
        row = sqlcore.run(cur, _summary(migrada)).fetchone()
        detalle = [dict(r) if isinstance(r, dict) else
                   dict(zip(("interview_id", "id", "created", "doctor_id",
                             "mismo_contenido", "vigente_id"), r))
                   for r in sqlcore.run(cur, _detail(migrada)).fetchall()]
    out = dict(row) if isinstance(row, dict) else dict(zip(("entrevistas", "a_marcar", "desde", "hasta"), row))
    out["a_anular"] = out["a_marcar"]
    out["ya_migrada"] = migrada
    out["detalle"] = detalle
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
    print(f"esquema con superseded_at: {'sí' if r['ya_migrada'] else 'no'}")
    print(f"entrevistas con más de una consulta vigente: {r['entrevistas']}")
    print(f"consultas que se MARCARÍAN como reemplazadas (no se borra ni se reescribe nada): {r['a_marcar']}")
    if r["entrevistas"]:
        print(f"creadas entre {r['desde']} y {r['hasta']}")
    for d in r["detalle"]:
        igual = "mismo contenido" if d["mismo_contenido"] else "contenido distinto"
        print(f"  entrevista {d['interview_id']}: consulta {d['id']} ({d['created']}, "
              f"médico {d['doctor_id']}) → la reemplaza la {d['vigente_id']} · {igual}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
