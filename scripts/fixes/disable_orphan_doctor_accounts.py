"""Disable the doctor accounts whose doctor is no longer in the catalog.

Orphans report 2026-10-24 (question 1), answered by the product owner's hub on 2026-10-25:
an account of role «médico» may point (`operators.doctor_id`) at a doctor that
exists nowhere — not in the data pack, not in the panel's overlay, not on the
public site (the doctor was removed from the catalog and the account, by the
rule «se deshabilitan, no se eliminan», stayed). Such an account that is still
ENABLED is switched off; accounts that are already disabled stay exactly as
they are (they are the record), and nothing is ever deleted. A doctor hidden in
the panel (`_borrado`) still exists in the catalog, so its account is left alone.

Dry run by default — it only prints what it would do. `--apply` writes (one
`UPDATE operators SET active = false` per account, through `db_operators.operator_update`).
Safe to run twice. It prints login names and doctor ids, no passwords, e-mails
or DNIs.

Run from the app directory (the container's /app), dry run first:
    python3 scripts/fixes/disable_orphan_doctor_accounts.py            # dry run
    python3 scripts/fixes/disable_orphan_doctor_accounts.py --apply    # write
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db_operators  # noqa: E402
from app.patient_web import catalog  # noqa: E402


def plan(doctor_ids, operators):
    """Pure decision: {disable: [operator], already_disabled: n, linked_ok: n,
    unlinked: n} — no database access. `doctor_ids` is every doctor id the
    catalog knows, hidden ones included; an operator is an orphan when it has
    a `medico_id` that is not in it."""
    known = {str(i) for i in doctor_ids}
    out = {"disable": [], "already_disabled": 0, "linked_ok": 0, "unlinked": 0}
    for op in operators:
        doctor_id = op.get("medico_id")
        if not doctor_id:
            out["unlinked"] += 1
        elif str(doctor_id) in known:
            out["linked_ok"] += 1
        elif not op.get("activo"):
            out["already_disabled"] += 1
        else:
            out["disable"].append(op)
    return out


def _catalog_ids():
    return [d["id"] for d in catalog.admin_list("medicos") if d.get("id")]


def run(apply=False, emit=print):
    decision = plan(_catalog_ids(), db_operators.operators_list())
    report = {"modo": "aplicado" if apply else "simulacion",
              "a_deshabilitar": len(decision["disable"]), "deshabilitadas_ahora": 0,
              "ya_deshabilitadas_sin_medico": decision["already_disabled"],
              "con_medico_en_catalogo": decision["linked_ok"], "sin_vinculo": decision["unlinked"]}
    for op in decision["disable"]:
        emit("deshabilitar  %s (médico %s no está en el catálogo)" % (op["usuario"], op["medico_id"]))
        if apply:
            db_operators.operator_update(op["usuario"], activo=False)
            report["deshabilitadas_ahora"] += 1
    emit(json.dumps(report, ensure_ascii=False))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    run(apply=args.apply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
