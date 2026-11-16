"""Which ids stored in the database are no longer in the catalog (schema
review №10, 2026-10-24). Read only.

Doctors, services and centers are JSON documents (the data pack plus the
panel's overlay in the `catalog` table), so the database cannot check the ids
other tables keep about them, and the panel never changes an id. An id only
disappears when a pack update renames or drops it — then its appointments fall
out of the lists and the reports without any error. `broken_references()`
lists those ids so a person decides before the update reaches patients;
`scripts/auditar_referencias_catalogo.py` prints it.

A disabled entry is NOT broken (a request keeps its doctor after the doctor is
switched off); only an id the catalog has never heard of is. The output holds
catalog ids and row counts — no patient data.
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as _pg

from app.core import db
from app.core import schema_model as sm
from app.core import sqlcore

# (table, column, catalog type). Complete by construction: `test_catalog_refs`
# compares it with every column of the real schema that holds such an id.
REFERENCES = (
    ("booking_requests", "service_id", "servicios"),
    ("booking_requests", "center_slug", "centros"),
    ("booking_requests", "assigned_doctor_id", "medicos"),
    ("booking_requests", "slot_doctor_id", "medicos"),
    ("appointment_offers", "service_id", "servicios"),
    ("appointment_offers", "center_slug", "centros"),
    ("day_queue_events", "center_slug", "centros"),
    ("day_queue_events", "assigned_doctor_id", "medicos"),
    ("waitlist", "service_id", "servicios"),
    ("waitlist", "center_slug", "centros"),
    ("slot_holds", "service_id", "servicios"),
    ("interviews", "doctor_id", "medicos"),
    ("consultations", "doctor_id", "medicos"),
    ("patient_summaries", "doctor_id", "medicos"),
    ("operators", "doctor_id", "medicos"),
)


def _known(tipo):
    from app.patient_web import catalog
    return catalog.known_ids(tipo)


def broken_references(known=None):
    """[{table, column, tipo, id, rows}] — one entry per id a table keeps that
    its catalog does not have, most rows first. `known` ({tipo: set of ids})
    replaces the live catalog (tests). A catalog or database that cannot be
    read raises: an audit must not answer «all good» for lack of data."""
    known = known or {}
    cache = {}
    out = []
    with db._connect() as conn, conn.cursor() as cur:
        sqlcore.run(cur, sa.text("SET TRANSACTION READ ONLY"))
        for table, column, tipo in REFERENCES:
            if tipo not in cache:
                cache[tipo] = known[tipo] if tipo in known else _known(tipo)
            # table/column come from the constant above (and the model), never from input
            col = sm.metadata.tables[table].c[column]
            ids = sa.literal(sorted(cache[tipo]), _pg.ARRAY(sa.Text))
            # `<> ALL(ids)` is `NOT (= ANY(ids))` for a non-null value; sa.not_() on `== any_()`
            # would compile to `!= ANY`, which means something else
            rows = sqlcore.run(cur, sa.select(col.label("id"), sa.func.count().label("rows")).where(
                col.is_not(None), col != sa.literal_column("''"), col != sa.all_(ids),
            ).group_by(col).order_by(sa.func.count().desc(), col)).fetchall()
            out.extend({"table": table, "column": column, "tipo": tipo,
                        "id": r["id"], "rows": r["rows"]} for r in rows)
    return out
