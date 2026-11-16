"""Staff accounts from one installation to another, without a single secret.

pinamar.humanrounds.org → salud.pinamar.gob.ar (the product owner, 2026-10-26): the
hospital's staff were given accounts on the test installation (the list Salud
sent on 2026-10-18); the municipal installation lets staff in only through its
portal (TURNOS_STAFF_PORTAL_LOGIN=1), matching an account by email the first
time and by the portal account afterwards (auth/staff_portal.py). So what
travels is who each person is and what they may do — login name, role, email,
linked doctor, centers, permission overrides, active or not — and never how
they used to log in: no password hash, no 2FA secret, no passkey, no calendar
feed token, no portal binding.

  export  — run on the source installation; writes a JSON file (personal
            data of staff: hand it over as a file, never commit it)
  import  — run on the target installation by whoever operates it

The import is a dry run unless `--apply`. Idempotent: an account that already
exists there (same login name, or same email in any case) is left exactly as
it is; a second run changes nothing. It never guesses: an entry whose doctor
is not in the target catalog, whose role the target does not have, or whose
email two entries claim is listed and not created. A created account gets a
random password nobody learns — the portal is the only way in — and enters
once its person logs into the portal with that same email.

Prints login names, actions and counts only; never an email, a password or a
secret.

Run from the app directory (the container's /app):
    python3 scripts/fixes/staff_transfer.py export --out /tmp/personal.json --skip-username qa.prueba
    python3 scripts/fixes/staff_transfer.py import /tmp/personal.json            # shows the plan
    python3 scripts/fixes/staff_transfer.py import /tmp/personal.json --apply    # writes
"""
import argparse
import datetime
import json
import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import db  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_auth  # noqa: E402
from app.core import permissions  # noqa: E402

FORMAT = "turnos-staff-transfer"
VERSION = 1
# Everything an entry carries — an allowlist, so a new column on the
# operators table never leaks into the file by itself.
FIELDS = ("usuario", "rol", "activo", "email", "medico_id", "centros",
          "permisos_agregados", "permisos_quitados")


def _entry(op):
    email = str(op.get("email") or "").strip() or None
    return {
        "usuario": op["usuario"],
        "rol": op.get("rol"),
        "activo": bool(op.get("activo")),
        "email": email,
        "medico_id": op.get("medico_id") or None,
        "centros": list(op.get("centros") or []),
        "permisos_agregados": list(op.get("permisos_agregados") or []),
        "permisos_quitados": list(op.get("permisos_quitados") or []),
    }


def export(path, skip_usernames=(), emit=print):
    """Write every staff account of this installation (minus `skip_usernames`) to `path`."""
    if os.path.exists(path):
        emit("ERROR: el archivo ya existe — elegí otro nombre")
        return 2
    skip = {u.casefold() for u in skip_usernames}
    everyone = db_operators.operators_list()
    people = [_entry(op) for op in everyone if op["usuario"].casefold() not in skip]
    data = {"formato": FORMAT, "version": VERSION,
            "exportado": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "personal": people}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    roles = {}
    for p in people:
        roles[p["rol"]] = roles.get(p["rol"], 0) + 1
    emit(json.dumps({"modo": "exportado", "personas": len(people), "omitidas": len(everyone) - len(people),
                     "por_rol": dict(sorted(roles.items())),
                     "sin_email": sum(1 for p in people if not p["email"])}, ensure_ascii=False))
    return 0


def _doctor_ids():
    """Every doctor id the target catalog knows (disabled and incomplete included)."""
    from app.patient_web import catalog
    return catalog.known_ids("medicos")


def _center_ids():
    """Every center id the target catalog knows (disabled included)."""
    from app.patient_web import catalog
    return catalog.known_ids("centros")


def _overrides_ok(p):
    """The entry's permission overrides as the target would store them, or None
    when the target would refuse them (an unknown name, one both granted and
    denied) — checked in the plan, so the dry run shows it and --apply never
    stops halfway through an account."""
    try:
        permissions.clean_overrides(p["permisos_agregados"] or [], p["permisos_quitados"] or [])
    except (permissions.RoleError, TypeError):
        return False
    return True


def _read(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("formato") != FORMAT or data.get("version") != VERSION:
        raise ValueError("no es un archivo de personal de esta versión")
    people = data.get("personal")
    if not isinstance(people, list):
        raise ValueError("falta la lista de personal")
    out = []
    for p in people:
        if not isinstance(p, dict) or not str(p.get("usuario") or "").strip() or not p.get("rol"):
            raise ValueError("hay una entrada sin usuario o sin rol")
        out.append({k: p.get(k) for k in FIELDS})
    return out


def _email_key(email):
    return str(email or "").strip().casefold()


def plan(people, operators, doctor_ids, role_ok, center_ids=None):
    """Pure decision, one (action, entry, reason) per entry: action is
    `create`, `exists` or `conflict`. No database access. `center_ids`: the
    target's centers (None: not checked)."""
    names = {o["usuario"].casefold() for o in operators}
    emails = {_email_key(o.get("email")) for o in operators if _email_key(o.get("email"))}
    claimed = {}
    for p in people:
        key = _email_key(p["email"])
        if key:
            claimed[key] = claimed.get(key, 0) + 1
    linked = {str(o.get("medico_id")) for o in operators if o.get("medico_id")}
    out = []
    for p in people:
        key = _email_key(p["email"])
        if p["usuario"].casefold() in names or (key and key in emails):
            out.append(("exists", p, "ya_existe"))
        elif key and claimed[key] > 1:
            out.append(("conflict", p, "email_repetido"))
        elif not role_ok(p["rol"]):
            out.append(("conflict", p, "rol_desconocido"))
        elif p["medico_id"] and p["rol"] == "medico" and str(p["medico_id"]) not in doctor_ids:
            out.append(("conflict", p, "medico_no_esta_en_el_catalogo"))
        elif p["medico_id"] and p["rol"] == "medico" and str(p["medico_id"]) in linked:
            out.append(("conflict", p, "medico_ya_tiene_cuenta"))
        elif not _overrides_ok(p):
            out.append(("conflict", p, "permisos_invalidos"))
        elif center_ids is not None and any(str(c) not in center_ids for c in (p["centros"] or [])):
            out.append(("conflict", p, "centro_no_esta_en_el_catalogo"))
        else:
            out.append(("create", p, ""))
    return out


def import_file(path, apply=False, emit=print):
    try:
        people = _read(path)
    except (OSError, ValueError) as exc:
        emit("ERROR: %s" % exc)
        return 2
    if os.environ.get("TURNOS_STAFF_PORTAL_LOGIN") != "1":
        emit("AVISO: esta instalación no entra por el portal (TURNOS_STAFF_PORTAL_LOGIN): "
             "las cuentas creadas necesitan que un administrador les dé una contraseña")
    decisions = plan(people, db_operators.operators_list(), {str(i) for i in _doctor_ids()},
                     permissions.role_is_assignable, {str(i) for i in _center_ids()})
    report = {"modo": "aplicado" if apply else "simulacion", "creados": 0, "ya_existian": 0,
              "conflictos": 0, "sin_email": 0}
    for action, p, reason in decisions:
        if action == "exists":
            emit("existe    %s" % p["usuario"])
            report["ya_existian"] += 1
            continue
        if action == "conflict":
            emit("CONFLICTO %s — %s (no se crea)" % (p["usuario"], reason))
            report["conflictos"] += 1
            continue
        emit("crear     %s (%s, %s%s)" % (p["usuario"], p["rol"], "activa" if p["activo"] else "inactiva",
                                          "" if p["email"] else ", sin email: no entra hasta cargarlo"))
        if apply:
            # One transaction: the account, its overrides and its 2FA deadline
            # land together or not at all — never an account without the
            # restrictions it was exported with (a second run would call it
            # «existe» and leave it so).
            try:
                with db._connect() as conn:
                    db_operators.operator_create(
                        p["usuario"], secrets.token_urlsafe(32), p["rol"], None,
                        medico_id=(p["medico_id"] if p["rol"] == "medico" else None),
                        email=p["email"], centros=p["centros"] or None, activo=bool(p["activo"]),
                        connection=conn)
                    if p["permisos_agregados"] or p["permisos_quitados"]:
                        permissions.set_overrides(p["usuario"], p["permisos_agregados"] or [],
                                                  p["permisos_quitados"] or [], connection=conn)
                    # Same as the first-admin script: the portal is the second factor.
                    db_auth.admin_security_set_deadline(p["usuario"], None, connection=conn)
            except db_operators.OperatorConflict as exc:
                emit("CONFLICTO %s — %s (otro proceso la creó mientras tanto)" % (p["usuario"], exc))
                report["conflictos"] += 1
                continue
        report["creados"] += 1
        if not p["email"]:
            report["sin_email"] += 1
    emit(json.dumps(report, ensure_ascii=False))
    return 1 if report["conflictos"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export", help="write this installation's staff to a file")
    exp.add_argument("--out", required=True, help="new file to write (it is never overwritten)")
    exp.add_argument("--skip-username", action="append", default=[],
                     help="an account that must not travel (test, ours); repeatable")
    imp = sub.add_parser("import", help="create the file's staff on this installation")
    imp.add_argument("file")
    imp.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    if args.cmd == "export":
        return export(args.out, args.skip_username)
    return import_file(args.file, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
