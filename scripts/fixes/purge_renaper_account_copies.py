"""Copies of real accounts out of the identity simulator's registry (2026-10-23).

Until 2026-10-23 the identity simulator (`connectors/renaper/renaper.py`,
`authenticate`), logged in with the DNI of an account that already existed,
copied that account's name, birth date, phone, email and address into its
registry of test people — the `catalog` rows of type
`renaper_test_identity` with `origen = 'cuenta_existente'` — and the
simulator lists that registry to whoever sees it. The simulator no longer
does it (it refuses a real account); this script removes the copies already
made. Pinamar's database had 11 registry rows on 2026-10-21 (problems.md №5).

What it does with each registry row:
  1. `origen = 'cuenta_existente'` — a copy of a real account: deleted. Only
     the copy goes; the account itself, its requests and its history are
     never touched.
  2. Same, but the DNI has imported clinical history
     (`clinical_history_records`) — listed and kept unless
     `--incluir-con-historia`: that person's case waits for a human decision
     (problems.md №5).
  3. Any other origin (`predefinida`, `aleatoria`, `manual`) — a test person,
     kept. Counted apart when an account with that DNI exists: most likely
     opened through the simulator (a TEST account the reports count as
     real), but a matching DNI does not prove it — a human checks those.

Dry run by default — it only reports. `--apply` deletes. Safe to run twice.
Prints counts only: no DNI, name, phone or email ever reaches the output.

Run from the app directory (the container's /app):
    python3 scripts/fixes/purge_renaper_account_copies.py            # dry run
    python3 scripts/fixes/purge_renaper_account_copies.py --apply    # delete
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlalchemy as sa  # noqa: E402

from app.core import db, schema_model, sqlcore  # noqa: E402
from app.core import db_catalog  # noqa: E402

CATALOG_TYPE = "renaper_test_identity"
COPY_ORIGIN = "cuenta_existente"


def plan(rows, account_dnis, history_dnis, include_history=False):
    """Pure decision: {delete: [id], kept_history: [id], test_people: n,
    test_accounts: n, total: n} — no database access. `rows` are catalog
    rows ({id, doc}); the two sets hold DNIs."""
    out = {"delete": [], "kept_history": [], "test_people": 0,
           "test_accounts": 0, "total": len(rows)}
    for row in rows:
        doc = row.get("doc") or {}
        dni = str(row.get("id") or doc.get("dni") or "")
        if doc.get("origen") == COPY_ORIGIN:
            if dni in history_dnis and not include_history:
                out["kept_history"].append(row.get("id"))
            else:
                out["delete"].append(row.get("id"))
            continue
        out["test_people"] += 1
        if dni in account_dnis:
            out["test_accounts"] += 1
    return out


def _dnis_with(table, dnis):
    if not dnis:
        return set()
    t = schema_model.metadata.tables[table]
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t.c.dni).distinct().where(t.c.dni.in_(list(dnis)))).fetchall()
        return {r["dni"] for r in rows}


def run(apply=False, include_history=False, emit=print):
    rows = db_catalog.catalog_list(CATALOG_TYPE)
    dnis = {str(r.get("id") or "") for r in rows}
    decision = plan(rows, _dnis_with("users", dnis),
                    _dnis_with("clinical_history_records", dnis), include_history)
    deleted = 0
    if apply:
        for id_ in decision["delete"]:
            db_catalog.catalog_delete_row(CATALOG_TYPE, id_)
            deleted += 1
    report = {
        "modo": "aplicado" if apply else "simulacion",
        "filas_registro": decision["total"],
        "copias_de_cuentas_reales": len(decision["delete"]) + len(decision["kept_history"]),
        "copias_a_borrar": len(decision["delete"]),
        "copias_borradas": deleted,
        "copias_con_historia_conservadas": len(decision["kept_history"]),
        "personas_de_prueba": decision["test_people"],
        "personas_de_prueba_con_cuenta_mismo_dni": decision["test_accounts"],
    }
    emit(json.dumps(report, ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    parser.add_argument("--incluir-con-historia", action="store_true",
                        help="also delete copies whose DNI has imported clinical history")
    args = parser.parse_args(argv)
    run(apply=args.apply, include_history=args.incluir_con_historia)
    return 0


if __name__ == "__main__":
    sys.exit(main())
