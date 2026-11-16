"""Every doctor has an account — the one-time backfill (the product owner, 2026-10-19).

Since «médico = usuario», creating a doctor creates its account in the same
transaction. Doctors created before that may have none. This script gives
each of them one, after first reconciling with accounts that already exist:

  1. A doctor already linked to an account is left alone.
  2. A doctor with no account is matched against the UNLINKED «médico»
     accounts by email (same address, any case) or by login name
     (`nombre.apellido`, the shape of the 2026-10-18 accounts, a trailing
     digit allowed). Exactly one candidate that matches no other doctor →
     linked. More than one → listed as ambiguous, never guessed.
  3. A doctor whose login name already belongs to an account linked to
     ANOTHER doctor is listed as a possible duplicate record, never given a
     second account. Generic role entries (`rol`: «Enfermería») get none.
  4. Otherwise a new account is created INACTIVE and «sin invitar»: nobody
     can log in with it until an admin invites the doctor from «Médicos».

Dry run by default — it only prints what it would do. `--apply` writes.
Safe to run twice and while the panel is in use: the database allows one
account per doctor (operators_doctor_id_uniq), so a doctor created in the
meantime is simply counted as already linked.

Run from the app directory (the container's /app):
    python3 scripts/fixes/backfill_doctor_accounts.py            # dry run
    python3 scripts/fixes/backfill_doctor_accounts.py --apply    # write
Prints counts and one line per doctor touched; no passwords or emails.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db_operators  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.admin_panel import admin_users  # noqa: E402


def _login_key(usuario):
    """«pedro.actisbernasconi2» → «pedro.actisbernasconi»."""
    return re.sub(r"\d+$", "", str(usuario or "").strip().lower())


def plan(doctors, operators):
    """Pure decision: {already, link: [(doctor, operator)], create: [doctor],
    ambiguous: [(doctor, [usernames])], duplicates: [(doctor, operator)],
    roles: n} — no database access.

    A catalog entry marked `rol` («Enfermería», «Técnico/a») is a generic
    role, not a person: it gets no account. A doctor whose login name is
    already the login of an account linked to ANOTHER doctor is most likely a
    duplicate record of the same person (pinamar, 2026-10-19: «martin-galvan»
    next to «galvan-martin»): listed for a human, never given a second
    account."""
    roles = [d for d in doctors if d.get("rol")]
    doctors = [d for d in doctors if not d.get("rol")]
    linked_ids = {str(o.get("medico_id")) for o in operators if o.get("medico_id")}
    unlinked = [o for o in operators
                if o.get("rol") == "medico" and not o.get("medico_id")]
    linked_by_login = {}
    for o in operators:
        if o.get("medico_id"):
            linked_by_login.setdefault(_login_key(o.get("usuario")), o)
    pending = [d for d in doctors if str(d.get("id")) not in linked_ids]

    def candidates(doctor):
        email = str(doctor.get("email") or "").strip().lower()
        base = admin_users.doctor_username_base(doctor)
        named = base != "medico.nuevo"
        return [o for o in unlinked
                if (email and str(o.get("email") or "").strip().lower() == email)
                or (named and _login_key(o.get("usuario")) == base)]

    matches = {str(d["id"]): candidates(d) for d in pending}
    claims = {}
    for doctor_id, found in matches.items():
        for o in found:
            claims.setdefault(o["usuario"], set()).add(doctor_id)

    out = {"already": len(doctors) - len(pending), "link": [], "create": [], "ambiguous": [],
           "duplicates": [], "roles": len(roles)}
    for doctor in pending:
        found = matches[str(doctor["id"])]
        base = admin_users.doctor_username_base(doctor)
        twin = linked_by_login.get(base) if base != "medico.nuevo" else None
        if not found and twin is not None:
            out["duplicates"].append((doctor, twin))
        elif not found:
            out["create"].append(doctor)
        elif len(found) == 1 and claims[found[0]["usuario"]] == {str(doctor["id"])}:
            out["link"].append((doctor, found[0]))
        else:
            out["ambiguous"].append((doctor, sorted(o["usuario"] for o in found)))
    return out


def _doctors():
    return [d for d in catalog.admin_list("medicos") if not d.get("_borrado") and d.get("id")]


def run(apply=False, emit=print):
    doctors = _doctors()
    decision = plan(doctors, db_operators.operators_list())
    report = {"modo": "aplicado" if apply else "simulacion",
              "medicos": len(doctors) - decision["roles"], "roles_sin_cuenta": decision["roles"],
              "ya_vinculados": decision["already"],
              "vinculados_ahora": 0, "creados_ahora": 0,
              "ambiguos": len(decision["ambiguous"]),
              "posibles_duplicados": len(decision["duplicates"]), "carrera": 0}
    for doctor, operator in decision["link"]:
        emit("vincular  %s -> %s" % (doctor["id"], operator["usuario"]))
        if apply:
            try:
                db_operators.operator_update(operator["usuario"], rol="medico",
                                   medico_id=str(doctor["id"]), link_only=True)
            except db_operators.OperatorConflict:
                report["carrera"] += 1
                continue
        report["vinculados_ahora"] += 1
    for doctor in decision["create"]:
        if apply:
            try:
                item = admin_users.provision_doctor_account(doctor)
            except db_operators.OperatorConflict:
                # Created (with its account) while this ran: already linked.
                report["carrera"] += 1
                continue
            emit("crear     %s -> %s (inactiva, sin invitar)" % (doctor["id"], item["usuario"]))
        else:
            emit("crear     %s -> %s (inactiva, sin invitar)" % (
                doctor["id"], admin_users._available_username(admin_users.doctor_username_base(doctor))))
        report["creados_ahora"] += 1
    for doctor, usernames in decision["ambiguous"]:
        emit("AMBIGUO   %s: %s — revisar a mano" % (doctor["id"], ", ".join(usernames)))
    for doctor, twin in decision["duplicates"]:
        emit("DUPLICADO %s: «%s» ya es la cuenta de %s — ¿el mismo médico cargado dos veces? revisar a mano"
             % (doctor["id"], twin["usuario"], twin["medico_id"]))
    if apply:
        catalog.invalidate()
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
