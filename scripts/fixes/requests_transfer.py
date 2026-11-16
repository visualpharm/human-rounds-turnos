"""Live patient requests from one installation to another, without their side material.

pinamar.humanrounds.org → salud.pinamar.gob.ar (Salud, 2026-10-26: «Copiarlos
a salud.pinamar.gob.ar»): a few real appointments were given on the test
installation before the municipal one opened, and their slots must not be
handed to someone else there. What travels is each request as the desk sees
it — patient (DNI, name, birth date, sex, contact), service, center, doctor,
time, state, who resolved it, the patient's note — and nothing else: no order
file or its AI reading, no HSI ids of the other installation, no confirmation
link, no attendance. Only a pending request or an approved one still ahead
travels; a cancelled, rejected or past one stays home.

  export  — run on the source installation; writes a JSON file (patients'
            personal data: hand it over as a file, never commit it)
  import  — run on the target installation by whoever operates it

Which installation is which. Each installation names itself by the host of its
`TURNOS_PUBLIC_ORIGIN` (environment or .env, never the admin overrides). The
export writes that host into the file as its origin (`--origin` is optional and
must agree with it, so a typo cannot rename the source), and the import refuses
to run on the installation the file came from: same host, or any request of the
file that exists here under its own id with the same DNI and creation time. An
installation without a `TURNOS_PUBLIC_ORIGIN` can do neither.

The import is a dry run unless `--apply`. Idempotent: each request is created
with the key `traslado:<origin>:<id>`, so a second run finds it and changes
nothing. It never guesses. The plan, which the dry run shows in full, lists
and does not create:
  - a service, center or doctor the target catalog lacks, or has switched off
    or deleted (live ones only: a request must not be handed to a doctor or a
    service that gives no appointments), or a doctor still waiting for onboarding;
  - an approved request whose (doctor, time) is already taken by the same rule
    as `booking_requests_doctor_slot_uniq` — an approved request that is not an
    overbooking, same `assigned_doctor_id` and `appointment_at` — on the target
    or earlier in the same file (the file's first request keeps the slot);
  - a request the target already has under another key: same DNI, service and
    time (the appointment time, or the creation time of a request still waiting)
    among its pending and approved ones, or twice in the file.
The request and its history («creada», then «aprobada» by whoever approved it)
land in one transaction. No patient account is created: the patient's portal
login opens the account with that DNI and the request is there (cuentas,
`list_by_dni`).

A database or catalog failure while the plan is being made prints its class only and
exits 3 with nothing written. When `--apply` hits a database error on a request,
only its id and the error class (SQLSTATE, constraint name) are printed — the
driver's own message quotes the failing row, i.e. DNI, name and contact. The
request's transaction is rolled back whole. The run then goes on when the error belongs to that row (the slot or
key taken by someone meanwhile → conflict; any other constraint or data error →
`errores`) and stops at once for any other database failure (connection,
permissions, schema), since the next request would fail the same way: the rest
are counted as `sin_intentar`. Run it again after fixing the cause; what was
created stays and is reported as `ya_existian`.

Prints request ids, actions and counts only; never a name, a DNI, a birth date
or a contact. Exit code: 0 all good, 1 conflicts listed, 2 the file or the
installation is refused, 3 database errors.

Run from the app directory (the container's /app):
    python3 scripts/fixes/requests_transfer.py export --out /tmp/solicitudes.json --id 48 --id 51
    python3 scripts/fixes/requests_transfer.py import /tmp/solicitudes.json            # shows the plan
    python3 scripts/fixes/requests_transfer.py import /tmp/solicitudes.json --apply    # writes
"""
import argparse
import datetime
import json
import os
import sys
from urllib.parse import urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import psycopg  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from app.core import config, db, sqlcore  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_values  # noqa: E402
from app.core import schema_model as sm  # noqa: E402

FORMAT = "turnos-requests-transfer"
VERSION = 1
# Everything a request carries — an allowlist, so a new column never leaks
# into the file by itself.
FIELDS = ("dni", "first_name", "last_name", "birth_date", "sex", "contact", "service_id", "center_slug",
          "status", "appointment_at", "time_preference", "requester_dni", "assigned_doctor",
          "assigned_doctor_id", "slot_doctor_id", "resolved_by", "resolved_at", "created", "patient_note",
          "overbooking", "confirmation", "confirmation_at", "confirmation_channel", "reminder_at")
_DATES = ("birth_date",)
_TIMES = ("appointment_at", "resolved_at", "created", "confirmation_at", "reminder_at")
LIVE = ("pendiente", "aprobada")
# A database error that belongs to one request: the run goes on with the next.
_ROW_ERRORS = (psycopg.errors.IntegrityError, psycopg.errors.DataError)


class BadFile(ValueError):
    """The file cannot be used; the message never quotes its content."""


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _live(row, now):
    if row["status"] == "pendiente":
        return True
    return row["status"] == "aprobada" and row["appointment_at"] is not None and row["appointment_at"] > now


def _wire_value(key, value):
    if value is not None and key in _DATES + _TIMES:
        return value.isoformat()
    return value


def _host(name):
    """The host a name or a URL stands for, lower case, no port; "" if none."""
    name = str(name or "").strip()
    if "//" not in name:
        name = "//" + name
    try:
        return (urlsplit(name).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""


def _own_host():
    """The name of this installation (host of its `TURNOS_PUBLIC_ORIGIN`)."""
    return config.installation_host()


def export(path, ids, origin=None, emit=print):
    """Write the live requests among `ids` of this installation to `path`."""
    own = _own_host()
    if not own:
        emit("ERROR: esta instalación no tiene TURNOS_PUBLIC_ORIGIN — no puede nombrarse como origen")
        return 2
    if origin and _host(origin) != own:
        emit("ERROR: --origin no es esta instalación (%s) — se omite o se corrige" % own)
        return 2
    if os.path.exists(path):
        emit("ERROR: el archivo ya existe — elegí otro nombre")
        return 2
    t = sm.booking_requests
    wanted = sorted({int(i) for i in ids})
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t.c.id, *[t.c[f] for f in FIELDS])
                           .where(t.c.id.in_(wanted)).order_by(t.c.id)).fetchall()
    found = {r["id"]: r for r in rows}
    now = _now()
    out = []
    for sid in wanted:
        row = found.get(sid)
        if row is None:
            emit("#%s no existe" % sid)
        elif not _live(row, now):
            emit("#%s no_vigente (%s) — no viaja" % (sid, row["status"]))
        else:
            entry = {f: _wire_value(f, row[f]) for f in FIELDS}
            entry["origen_id"] = sid
            out.append(entry)
            emit("#%s %s" % (sid, row["status"]))
    data = {"formato": FORMAT, "version": VERSION, "origen": own,
            "exportado": now.isoformat(timespec="seconds"), "solicitudes": out}
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    emit(json.dumps({"modo": "exportado", "solicitudes": len(out), "omitidas": len(wanted) - len(out)},
                    ensure_ascii=False))
    return 0


def _parse(entry, key, parse, origen_id):
    """`parse(entry[key])`; a value that does not parse is named by request id and
    field only — never quoted (a birth date is personal data)."""
    try:
        value = parse(entry[key])
    except (ValueError, TypeError, OverflowError):
        raise BadFile("la solicitud #%s trae un valor ilegible en %s" % (origen_id, key)) from None
    if key in _TIMES and value is not None and value.tzinfo is None:
        raise BadFile("la solicitud #%s trae %s sin zona horaria" % (origen_id, key))
    return value


def _read(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or data.get("formato") != FORMAT or data.get("version") != VERSION:
        raise BadFile("no es un archivo de solicitudes de esta versión")
    origin = _host(data.get("origen"))
    rows = data.get("solicitudes")
    if not origin or not isinstance(rows, list):
        raise BadFile("falta el origen o la lista de solicitudes")
    out = []
    for r in rows:
        if not isinstance(r, dict) or not str(r.get("dni") or "").strip() or not r.get("origen_id"):
            raise BadFile("hay una solicitud sin DNI o sin id de origen")
        try:
            origen_id = int(r["origen_id"])
        except (ValueError, TypeError):
            raise BadFile("hay una solicitud con un id de origen ilegible") from None
        entry = {k: r.get(k) for k in FIELDS}
        for k in _DATES:
            entry[k] = _parse(entry, k, db_values._coerce_date, origen_id)
        for k in _TIMES:
            entry[k] = _parse(entry, k, db_values._coerce_dt, origen_id)
        entry["overbooking"] = bool(entry["overbooking"])
        entry["origen_id"] = origen_id
        out.append(entry)
    return origin, out


def _known(tipo):
    """Every id the target catalog of `tipo` ever had (disabled ones included)."""
    from app.patient_web import catalog
    return catalog.known_ids(tipo)


def _live_ids(tipo):
    """The ids of `tipo` the target catalog gives appointments with today."""
    from app.patient_web import catalog
    return catalog.live_ids(tipo)


def _key(origin, entry):
    return "traslado:%s:%s" % (origin, entry["origen_id"])


def _existing_keys(keys):
    t = sm.booking_requests
    if not keys:
        return set()
    with db._connect() as conn, conn.cursor() as cur:
        rows = sqlcore.run(cur, sa.select(t.c.idem_key).where(t.c.idem_key.in_(sorted(keys)))).fetchall()
    return {r["idem_key"] for r in rows}


def _is_source(rows):
    """True when a request of the file exists HERE under its own id with the same
    DNI and creation time: only the installation the file was exported from
    holds that — a second sign, next to the origin name, of running on the source.
    A row this script made (key `traslado:…`) keeps the file's DNI and creation time and
    may well get an id the source also used: it is not a sign."""
    t = sm.booking_requests
    ids = sorted({r["origen_id"] for r in rows})
    if not ids:
        return False
    with db._connect() as conn, conn.cursor() as cur:
        local = sqlcore.run(cur, sa.select(t.c.id, t.c.dni, t.c.created, t.c.idem_key)
                         .where(t.c.id.in_(ids))).fetchall()
    by_id = {r["id"]: r for r in local}
    for r in rows:
        mine = by_id.get(r["origen_id"])
        if (mine is not None and not str(mine["idem_key"] or "").startswith("traslado:")
                and r["created"] is not None and mine["created"] == r["created"]
                and str(mine["dni"] or "").strip() == str(r["dni"]).strip()):
            return True
    return False


def _time_key(r):
    """The «time» of a request for the duplicate check: its appointment, or the
    moment it was made while it still has none."""
    return r["appointment_at"] or r["created"]


def _target_state(rows):
    """What the target database already holds that can collide with the file:
    `slots` — the (doctor, time) pairs `booking_requests_doctor_slot_uniq` covers;
    `requests` — (DNI, service, time) of its pending and approved requests."""
    t = sm.booking_requests
    doctors = sorted({str(r["assigned_doctor_id"]) for r in rows if r["assigned_doctor_id"] is not None})
    dnis = sorted({str(r["dni"]).strip() for r in rows})
    with db._connect() as conn, conn.cursor() as cur:
        slots = sqlcore.run(cur, sa.select(t.c.assigned_doctor_id, t.c.appointment_at).where(
            t.c.status == "aprobada", sa.not_(t.c.overbooking), t.c.appointment_at.isnot(None),
            t.c.assigned_doctor_id.in_(doctors))).fetchall() if doctors else []
        same = sqlcore.run(cur, sa.select(t.c.dni, t.c.service_id, t.c.appointment_at, t.c.created).where(
            t.c.status.in_(LIVE), t.c.dni.in_(dnis))).fetchall() if dnis else []
    return {"slots": {(str(r["assigned_doctor_id"]), r["appointment_at"]) for r in slots},
            "requests": {(str(r["dni"]).strip(), str(r["service_id"]), _time_key(r)) for r in same}}


def _takes_slot(r):
    """Whether the request falls under `booking_requests_doctor_slot_uniq`: approved,
    not an overbooking, with a doctor (any non-NULL id, as the index reads it) and a time."""
    return (r["status"] == "aprobada" and not r["overbooking"] and r["assigned_doctor_id"] is not None
            and r["appointment_at"] is not None)


def plan(origin, rows, existing, live, known, target, now):
    """Pure decision, one (action, entry, reason) per request: `create`,
    `exists` or `conflict`. `live(tipo)` → ids the target catalog gives
    appointments with, `known(tipo)` → every id it ever had (to tell a switched-off
    one from one it never had); `target` → `_target_state` of the target database."""
    catalog = {tipo: (live(tipo), known(tipo)) for tipo in ("servicios", "centros", "medicos")}
    slots, requests = set(target["slots"]), set(target["requests"])
    out = []

    def missing(tipo, value, name):
        value = str(value)
        if value in catalog[tipo][0]:
            return None
        return "%s_%s" % (name, "deshabilitado" if value in catalog[tipo][1] else "no_esta_en_el_catalogo")

    for r in rows:
        doctors = {str(d) for d in (r["assigned_doctor_id"], r["slot_doctor_id"]) if d}   # both are stored
        if _key(origin, r) in existing:
            out.append(("exists", r, "ya_existe"))
            continue
        reason = None
        if r["status"] not in LIVE:
            reason = "estado_no_vigente"        # the value itself comes from the file: not echoed
        elif r["status"] == "aprobada" and (r["appointment_at"] is None or r["appointment_at"] <= now):
            reason = "turno_pasado"
        else:
            reason = (missing("servicios", r["service_id"], "servicio") or missing("centros", r["center_slug"], "centro")
                      or next((why for why in (missing("medicos", d, "medico") for d in sorted(doctors)) if why), None))
        if reason is None:
            same = (str(r["dni"]).strip(), str(r["service_id"]), _time_key(r))
            slot = (str(r["assigned_doctor_id"]), r["appointment_at"])
            if same in requests:
                reason = "duplicado_en_destino" if same in target["requests"] else "duplicado_en_el_archivo"
            elif _takes_slot(r) and slot in slots:
                reason = "horario_ocupado" if slot in target["slots"] else "horario_repetido_en_el_archivo"
            else:
                requests.add(same)
                if _takes_slot(r):
                    slots.add(slot)
        out.append(("conflict", r, reason) if reason else ("create", r, ""))
    return out


def _create(origin, r):
    """The request and its history in ONE transaction; returns the new id."""
    t = sm.booking_requests
    values = {f: r[f] for f in FIELDS}
    values["idem_key"] = _key(origin, r)
    with db._connect() as conn, conn.cursor() as cur:
        sid = sqlcore.run(cur, sa.insert(t).values(**values).returning(t.c.id)).fetchone()["id"]
        db_booking._log_appointment_event(cur, sid, "creada", "traslado", None, "pendiente", None, None)
        if r["status"] == "aprobada":
            db_booking._log_appointment_event(cur, sid, "aprobada", r["resolved_by"] or "traslado", "pendiente",
                                      "aprobada", None, r["appointment_at"])
        return sid


def _describe(exc):
    """Error class, SQLSTATE and constraint name of a database error — the only
    parts of it that are safe to print (its message quotes the failing row)."""
    parts = [type(exc).__name__]
    for value in (getattr(exc, "sqlstate", None), getattr(getattr(exc, "diag", None), "constraint_name", None)):
        if value:
            parts.append(str(value))
    return " ".join(parts)


def import_file(path, apply=False, emit=print):
    try:
        origin, rows = _read(path)
    except BadFile as exc:
        emit("ERROR: %s" % exc)
        return 2
    except json.JSONDecodeError:
        emit("ERROR: el archivo no es JSON")
        return 2
    except OSError as exc:
        emit("ERROR: no se pudo leer el archivo (%s)" % type(exc).__name__)
        return 2
    except (ValueError, TypeError) as exc:
        emit("ERROR: el archivo no se pudo interpretar (%s)" % type(exc).__name__)
        return 2
    own = _own_host()
    if not own:
        emit("ERROR: esta instalación no tiene TURNOS_PUBLIC_ORIGIN — no puede saber si el archivo es de otra")
        return 2
    try:
        if origin == own or _is_source(rows):
            emit("ERROR: el archivo viene de ESTA instalación (%s) — se importa en la instalación de destino" % own)
            return 2
        decisions = plan(origin, rows, _existing_keys({_key(origin, r) for r in rows}), _live_ids, _known,
                         _target_state(rows), _now())
    except (psycopg.Error, db.DbUnavailable, RuntimeError) as exc:   # the database or the catalog cannot be read
        emit("ERROR: no se pudo leer la base o el catálogo (%s) — no se escribió nada" % _describe(exc))
        return 3
    report = {"modo": "aplicado" if apply else "simulacion", "creadas": 0, "ya_existian": 0, "conflictos": 0,
              "errores": 0, "sin_intentar": 0}
    stopped = False
    for action, r, reason in decisions:
        label = "#%s" % r["origen_id"]
        if action == "exists":
            emit("existe    %s" % label)
            report["ya_existian"] += 1
            continue
        if action == "conflict":
            emit("CONFLICTO %s — %s (no se crea)" % (label, reason))
            report["conflictos"] += 1
            continue
        if stopped:
            emit("sin intentar %s" % label)
            report["sin_intentar"] += 1
            continue
        if apply:
            try:
                sid = _create(origin, r)
            except psycopg.errors.UniqueViolation as exc:
                name = getattr(getattr(exc, "diag", None), "constraint_name", "") or ""
                why = "horario_ocupado" if "doctor_slot" in name else "ya_existe_en_otra_corrida"
                emit("CONFLICTO %s — %s (no se crea)" % (label, why))
                report["conflictos"] += 1
                continue
            except (psycopg.Error, db.DbUnavailable) as exc:
                emit("ERROR     %s — %s (no se crea)" % (label, _describe(exc)))
                report["errores"] += 1
                stopped = not isinstance(exc, _ROW_ERRORS)
                continue
            emit("creada    %s → #%s (%s)" % (label, sid, r["status"]))
        else:
            emit("crear     %s (%s)" % (label, r["status"]))
        report["creadas"] += 1
    emit(json.dumps(report, ensure_ascii=False))
    if report["errores"]:
        return 3
    return 1 if report["conflictos"] else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    exp = sub.add_parser("export", help="write this installation's live requests to a file")
    exp.add_argument("--out", required=True, help="new file to write (it is never overwritten)")
    exp.add_argument("--origin", help="optional: must name this installation (its TURNOS_PUBLIC_ORIGIN host)")
    exp.add_argument("--id", action="append", type=int, required=True, help="a request id; repeatable")
    imp = sub.add_parser("import", help="create the file's requests on this installation")
    imp.add_argument("file")
    imp.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    if args.cmd == "export":
        return export(args.out, args.id, args.origin)
    return import_file(args.file, apply=args.apply)


if __name__ == "__main__":
    sys.exit(main())
