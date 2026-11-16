#!/usr/bin/env python3
"""tests/e2e/journey_fixture.py — extra seeding and read-back commands for the
cross-role journey specs (tests/e2e/journey-j1*.spec.mjs, helpers in
tests/e2e/lib/journey.mjs).

Same doctrine as fixture.py: every write goes through the app's own functions
(db_operators.operator_create, db_accounts.create_user, intake_admin.set_doctor_override, …),
every read goes through the app's own read path, never a hand-built row. The
throwaway database is the one lib/harness.mjs already created with
`fixture.py setup`; this script only resolves its URL and talks to it.

    python3 journey_fixture.py <command> --db <name> [--spec <json>] [--arg value …]

Prints ONE JSON object on stdout; `{"error": …}` and exit 1 on failure. No
secret is ever printed (passwords are inputs, tokens are only the patient
session cookie a test needs to act as that patient)."""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tests/: _env

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")

import fixture as base  # noqa: E402  (tests/e2e/fixture.py: same DB-name guard and URL resolution)
from app.core import db_wire  # noqa: E402


def _out(obj):
    print(json.dumps(obj, default=str))


def _bind(db_name):
    base._require_test_db_name(db_name)
    _maint, test_url = base._urls_for(db_name)
    _env.process_set(DATABASE_URL=test_url)
    from app.core import db
    from app.core import db_schema
    db_schema._ensured.clear()
    return db


def _iso(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else value


def _request_view(row):
    """The safe, assertion-oriented subset of a booking_requests row."""
    if not row:
        return None
    ai = row.get("ai_json") or {}
    return {
        "id": row.get("id"),
        "estado": row.get("estado"),
        "servicio_id": row.get("servicio_id"),
        "centro_slug": row.get("centro_slug"),
        "medico_asignado": row.get("medico_asignado"),
        "medico_asignado_id": row.get("medico_asignado_id"),
        "slot_medico_id": row.get("slot_medico_id"),
        "turno_dt": _iso(row.get("turno_dt")),
        "dni": row.get("dni"),
        "nombre": row.get("nombre"),
        "apellido": row.get("apellido"),
        "contacto": row.get("contacto"),
        "solicitante_dni": row.get("solicitante_dni"),
        "sobreturno": bool(row.get("sobreturno")),
        "nota_paciente": row.get("nota_paciente"),
        "preferencia_horario": row.get("preferencia_horario"),
        "ai_verdict": ai.get("verdict"),
        "motivo": row.get("motivo_rechazo") or row.get("motivo"),
        "asistencia": row.get("asistencia"),
    }


def cmd_seed_operators(args):
    """--spec [{"usuario","password","rol","medico_id"?,"email"?,"centros"?}]"""
    from app.core import db_operators
    db = _bind(args.db)
    made = []
    for item in json.loads(args.spec):
        db_operators.operator_create(
            item["usuario"], password=item["password"], rol=item["rol"],
            medico_id=item.get("medico_id"), email=item.get("email"),
            centros=item.get("centros"))
        made.append(item["usuario"])
    _out({"ok": True, "operators": made})


def cmd_seed_patient(args):
    """--spec {"dni","nombre","apellido","nacimiento","sexo","contacto","email","password"}
    Creates the account the way fixture.py --account does and opens a session."""
    from app.core import db_accounts
    db = _bind(args.db)
    p = json.loads(args.spec)
    db_accounts.create_user(
        p["dni"], p["nombre"], p["apellido"], p.get("nacimiento", "1990-06-15"),
        p.get("sexo", "F"), p.get("contacto", "+5492254100099"),
        p.get("password", "e2e-journey-throwaway-pass"), email=p.get("email"))
    _out({"ok": True, "dni": p["dni"], "session": db_accounts.create_session(p["dni"])})


def cmd_seed_visit(args):
    """--spec {"dni","nombre","apellido","doctor_id","servicio_id","centro","when"?, "interview"?, "account"?, "asistencia"?}

    One approved appointment on a doctor, the way the panel's approval leaves it,
    plus (default) the pre-visit interview the patient finished and the doctor's
    opt-in for it. `when` is "today" (09:00 installation time, even if that is
    already past -- the day queue lists the whole day) or "+N"/"-N" days (10:00);
    `at` ("HH:MM", installation time) overrides the hour. One doctor can hold
    one appointment per minute, so a journey seeding several visits spreads them.
    With "account": true the patient also gets an account and a session.
    Same functions fixture.py --doctor-patients uses; nothing is hand-built."""
    from app.core import db_accounts
    from app.core import db_booking
    from app.core import db_clinical
    db = _bind(args.db)
    from datetime import datetime, timedelta
    from app.core import tz
    s = json.loads(args.spec)
    dni = s["dni"]
    out = {"dni": dni}
    if s.get("account"):
        db_accounts.create_user(dni, s["nombre"], s["apellido"], s.get("nacimiento", "1984-03-02"),
                       s.get("sexo", "F"), s.get("account_contacto", s.get("contacto", "+5492254200001")),
                       "e2e-journey-throwaway-pass", email=s.get("email"))
        out["session"] = db_accounts.create_session(dni)
    sid = db_booking.create_booking_request(
        dni, s["nombre"], s["apellido"], s.get("nacimiento", "1984-03-02"), s.get("contacto"),
        s["servicio_id"], s["centro"], solicitante_dni=dni)
    if s.get("pending"):
        # Still waiting for the desk; optionally already parked on a doctor (the queue's
        # suggestion / the desk's combo), exactly what /api/admin/requests/<id>/assign stores.
        if s.get("doctor_id"):
            db_booking.assign_pending_booking_request(sid, s.get("doctor_name"), s["doctor_id"])
        out["request_id"] = sid
        _out(out)
        return
    now = datetime.now(tz.tzinfo())
    when = s.get("when", "today")
    hour, minute = (int(part) for part in s.get("at", "09:00" if when == "today" else "10:00").split(":"))
    day = now if when == "today" else now + timedelta(days=int(when.lstrip("+")))
    turno = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    db_booking.approve(sid, turno, {"estado": "skip"},
               medico_asignado=s.get("doctor_name"), medico_asignado_id=s["doctor_id"],
               resuelta_por="e2e-journey")
    out["request_id"] = sid
    if s.get("asistencia"):
        # A past visit with its attendance recorded ("presente"/"ausente"), the way the
        # desk's Atendió / No vino leaves it: what the attendance forecast reads.
        db_booking.mark_attendance(sid, s["asistencia"], "e2e-journey")
    if s.get("interview", True):
        from app.modules.intake import intake_admin
        iv = db_clinical.interview_create(
            doctor_id=s["doctor_id"], entry_mode="post_booking", request_id=sid, dni=dni,
            entered_name=s["nombre"], entered_surname=s["apellido"], matched=True)
        db_clinical.interview_update_analysis(iv["id"], "VERDE", "Synthetic journey interview.", {}, [], [], [], "fake-model")
        db_clinical.interview_set_status(iv["id"], "done")
        intake_admin.set_doctor_override(s["doctor_id"], True)
        out["interview_id"] = iv["id"]
    _out(out)


def cmd_seed_interview(args):
    """--spec {"doctor_id","dni"?,"nombre"?,"apellido"?} : a pre-visit interview that names a
    doctor and no appointment -- the kind of row that points at a doctor without being a booking."""
    from app.core import db_clinical
    db = _bind(args.db)
    s = json.loads(args.spec)
    iv = db_clinical.interview_create(
        doctor_id=s["doctor_id"], entry_mode="qr", dni=s.get("dni"),
        entered_name=s.get("nombre"), entered_surname=s.get("apellido"), matched=False)
    _out({"interview_id": iv["id"]})


def cmd_doctors(args):
    """The admin list of doctors the panel reads (disabled ones included, flagged),
    through the app's own catalog reader: [{id, nombre, borrado}]."""
    _bind(args.db)
    from app.patient_web import catalog
    items = catalog.admin_list("medicos")
    _out({"doctors": [{"id": d.get("id"), "nombre": d.get("nombre"), "borrado": bool(d.get("_borrado"))} for d in items]})


def cmd_doctor_of_interview(args):
    """--interview N : the doctor id an interview names (None when there is no such interview)."""
    db = _bind(args.db)
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT doctor_id FROM interviews WHERE id=%s", (int(args.interview),))
        row = cur.fetchone()
    _out({"found": bool(row), "doctor_id": (row["doctor_id"] if row else None)})


def cmd_doctor_intake(args):
    """--doctor <catalog id> --on 1|0 : the per-doctor pre-visit interview switch
    (OFF by default since 2026-10-21), written through the panel's own override."""
    _bind(args.db)
    from app.modules.intake import intake_admin
    intake_admin.set_doctor_override(args.doctor, args.on == "1")
    _out({"ok": True})


def cmd_seed_dependent(args):
    """--spec {"holder","dni","nombre","apellido","nacimiento","sexo"?,"vinculo"?} : a dependent saved on the
    holder's account the way the account page's POST saves it (the app's own store)."""
    _bind(args.db)
    from app.cuentas import accounts_store as store
    d = json.loads(args.spec)
    store.save_dependent(d["holder"], d["dni"], d["nombre"], d["apellido"], d["nacimiento"],
                         d.get("sexo", "M"), d.get("vinculo", "hijo"))
    _out({"ok": True})


def cmd_dependents(args):
    """--dni D : the dependents saved on the account of D, through the app's own store."""
    _bind(args.db)
    from app.cuentas import accounts_store as store
    _out({"dependents": [{"dni": d.get("dni"), "nombre": d.get("nombre"), "apellido": d.get("apellido"),
                          "vinculo": d.get("vinculo"), "sexo": d.get("sexo")}
                         for d in store.list_dependents(args.dni)]})


def cmd_requests(args):
    """--dni D : every booking request of that patient, newest first."""
    db = _bind(args.db)
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM booking_requests WHERE dni=%s OR requester_dni=%s ORDER BY id DESC",
                    (args.dni, args.dni))
        rows = [db_wire._wire(r, "booking_requests") for r in cur.fetchall()]
    _out({"requests": [_request_view(r) for r in rows]})


def cmd_request(args):
    """--id N : one booking request."""
    from app.core import db_booking
    db = _bind(args.db)
    _out({"request": _request_view(db_booking.get_booking_request(int(args.id)))})


def cmd_interview_of(args):
    """--request N : the interview bound to a request (id only — never the token)."""
    db = _bind(args.db)
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, doctor_id, status FROM interviews WHERE request_id=%s ORDER BY id DESC",
                    (int(args.request),))
        rows = cur.fetchall()
    out = []
    for r in rows:
        r = dict(r) if hasattr(r, "keys") else {"id": r[0], "doctor_id": r[1], "status": r[2]}
        out.append({"id": r.get("id"), "doctor_id": r.get("doctor_id"), "status": r.get("status")})
    _out({"interviews": out})


def cmd_consultations(args):
    """--interview N : every consultation row of an interview, newest first, with
    the issued-document facts a journey asserts (token presence only, never the token)."""
    db = _bind(args.db)
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM consultations WHERE interview_id=%s ORDER BY id DESC", (int(args.interview),))
        rows = [db_wire._wire(r, "consultations") for r in cur.fetchall()]
    _out({"consultations": [{
        "id": r.get("id"),
        "doctor_id": r.get("doctor_id"),
        "receta": bool(r.get("receta_token")),
        "orden": bool(r.get("orden_token")),
        "notas": r.get("notes"),
        "superseded": bool(r.get("superseded_at")) if "superseded_at" in r else None,
    } for r in rows]})


def cmd_tick_reminders(args):
    """--now <ISO instant> : runs ONE pass of the day-before reminder sweep with the
    given clock (notifications/reminders.tick), exactly the code the background
    worker runs, against the journey's database and the env this process inherits
    (the spec points the mail/WhatsApp endpoints at its local sinks)."""
    _bind(args.db)
    from datetime import datetime
    # The web process (and the queue worker's registry) has the booking module loaded, and the
    # mail/WhatsApp texts name the service through it: load it the same way before sweeping.
    from app.modules.turnos import booking_requests  # noqa: F401
    from app.notifications import reminders
    now = datetime.fromisoformat(args.now)
    _out({"processed": reminders.tick(now_utc=now)})


def cmd_consultation_contents(args):
    """--interview N : what each consultation row of an interview STORED (the doctor's choices and the
    composed documents), newest first, for a journey to compare with what the doctor entered:
    diagnoses / exams / meds / notes, plus the order's and the prescription's own lines."""
    db = _bind(args.db)
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM consultations WHERE interview_id=%s ORDER BY id DESC", (int(args.interview),))
        rows = [db_wire._wire(r, "consultations") for r in cur.fetchall()]

    def _doc(payload, *keys):
        payload = payload if isinstance(payload, dict) else {}
        return {key: payload.get(key) for key in keys}

    _out({"consultations": [{
        "id": r.get("id"),
        "diagnoses": r.get("diagnoses"),
        "exams": r.get("exams"),
        "meds": r.get("meds"),
        "notes": r.get("notes"),
        "receta": _doc(r.get("receta_json"), "lineas", "notas"),
        "orden": _doc(r.get("orden_json"), "estudios", "diagnosticos", "notas"),
    } for r in rows]})


def cmd_doctor_windows(args):
    """--doctor <catalog id> : the weekly windows saved on a doctor, the way the panel's editor
    stored them (through the app's own catalog reader): [{centro, dias, desde, hasta, cada_min, servicio}]."""
    _bind(args.db)
    from app.patient_web import catalog
    doctor = next((d for d in catalog.admin_list("medicos") if d.get("id") == args.doctor), None)
    windows = []
    for centro in ((doctor or {}).get("centros") or []):
        for franja in (centro.get("franjas") or []):
            windows.append({
                "centro": centro.get("centro"), "dias": sorted(franja.get("dias") or []),
                "desde": franja.get("desde"), "hasta": franja.get("hasta"),
                "cada_min": franja.get("cada_min"), "servicio": franja.get("servicio"),
            })
    _out({"found": doctor is not None, "windows": windows})


def cmd_attach_referral_file(args):
    """--request N --file PATH [--mime image/jpeg] : records an existing file as the referral of a
    request, through the app's own store (`db_booking.set_referral`) -- what an upload leaves on the row.
    The file is read by GET /api/admin/order/<id>; a request seeded by `seed-visit` has none."""
    from app.core import db_booking
    db = _bind(args.db)
    if not os.path.isfile(args.file):
        raise ValueError("no such file")
    db_booking.set_referral(int(args.request), os.path.abspath(args.file), args.mime or "image/jpeg")
    _out({"ok": True, "request_id": int(args.request)})


def cmd_reject(args):
    """--id N --motivo TEXT : the desk's rejection of a pending request, through the app's own
    status-CAS (db_booking.reject) -- the state a journey needs to prove a rejected request holds no place."""
    from app.core import db_booking
    db = _bind(args.db)
    row = db_booking.reject(int(args.id), args.motivo or "Rechazada en la prueba", resuelta_por="e2e-journey")
    _out({"ok": True, "estado": row.get("estado")})


def _flat_routes(routes):
    """FastAPI's included-router tree, flattened (same walk tests/test_roles_parity.py does)."""
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            yield from _flat_routes([(getattr(c, "route", None) or c) for c in route.effective_candidates()])
        else:
            yield route


def cmd_access_matrix(args):
    """The staff access contract the running app is built from, for the role-boundary journey:
      roles    {role: [permission, …]}  the built-in presets (core/permissions.BUILTIN_ROLES)
      routes   [{method, template, path, kind: "screen"|"api", needs: {role: "*"|permission|[alternatives]|null}}]
               every panel route (/admin/…, /api/admin/…) with the permission the central gate maps it to
               (`required_permission`, per role: a doctor's own editor is mapped differently), `path` being a
               concrete URL to knock on
      manifest [{id, path, roles}]  the admin sections that declare `roles` in modules/*/module.json
    Nothing is hand-listed here: a new route or section shows up in the journey's matrix on its own."""
    _bind(args.db)
    import glob
    from app import main as app_module
    from app.core import permissions as perm
    roles = {}
    for slug, preset in perm.BUILTIN_ROLES.items():
        roles[slug] = sorted(perm.effective(slug, preset["permissions"]))
    seen, routes = set(), []
    for route in _flat_routes(app_module.app.routes):
        template = getattr(route, "path", None)
        methods = sorted((getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"})
        if not template or not methods or not (template.startswith("/admin") or template.startswith("/api/admin")):
            continue
        # A segment the route resolves from a closed list needs a value from that list, or the
        # router answers 404 before it ever asks who is knocking.
        path = (template.replace("{tipo}/{sub}", "knowledge/properties").replace("{action}", "disable")
                .replace("{tipo}", "doctors").replace("{entity}", "doctors"))
        path = re.sub(r"\{[^}]+\}", "1", path)
        for method in methods:
            if (method, template) in seen:
                continue
            seen.add((method, template))
            needs = {}
            for role in roles:
                need = perm.required_permission(method, template, path, role=role)
                needs[role] = sorted(need) if isinstance(need, frozenset) else need
            routes.append({"method": method, "template": template, "path": path,
                           "kind": "api" if template.startswith("/api/") else "screen", "needs": needs})
    manifest = []
    for name in sorted(glob.glob(os.path.join(ROOT, "app", "modules", "*", "module.json"))):
        with open(name, encoding="utf-8") as fh:
            data = json.load(fh)
        for section in data.get("admin_sections", []):
            if section.get("roles") and (section.get("screen") or {}).get("path"):
                manifest.append({"id": section["id"], "path": section["screen"]["path"], "roles": section["roles"]})
    _out({"roles": roles, "routes": routes, "manifest": manifest})


def cmd_patient(args):
    """--dni D : the account of that patient as the app stores it (no password hash, no secrets):
    name, birth date, sex, phone and email."""
    from app.core import db_accounts
    db = _bind(args.db)
    u = db_accounts.get_user(args.dni) or {}
    _out({"patient": {k: _iso(u.get(k)) for k in ("dni", "nombre", "apellido", "nacimiento", "sexo", "contacto", "email")}
          if u else None})


def cmd_door(args):
    """--doctor D [--url U] [--token T] : the doctor's door-poster token as the catalog holds it (created on
    first ask, like the poster endpoint does), and what the poster must carry for a printed URL U: the QR
    drawing of U (intake_admin._qr_svg) and the SHA-256 of the PDF poster built for U. With --token T:
    the doctor id that token (given as a JSON string) opens (doctor_by_door_token), None when it opens nothing."""
    _bind(args.db)
    import hashlib
    from app.modules.intake import intake_admin
    token, doc = intake_admin.get_or_create_door_token(args.doctor)
    out = {"token": token}
    if args.url:
        out["qr_svg"] = intake_admin._qr_svg(args.url)
        # Pillow stamps the creation/modification second into the file: leave those two entries out.
        pdf = re.sub(rb"/(CreationDate|ModDate) \(D:[^)]*\)", b"", intake_admin.door_poster_pdf(doc, args.url))
        out["pdf_sha256"] = hashlib.sha256(pdf).hexdigest()
    if args.token:
        # JSON-quoted: a door token is URL-safe base64 and may start with "-", which argparse reads as a flag.
        owner = intake_admin.doctor_by_door_token(json.loads(args.token))
        out["owner"] = owner.get("id") if owner else None
    _out(out)


def cmd_seed_unreadable_referral(args):
    """--spec {"dni","nombre","apellido","servicio_id","centro","verdict"?} : a PENDING request that carries a
    referral file the AI reading rejected (`verdict`, default "ilegible"): the kind of request for which the
    desk's «Resolver según recomendación» recommends REJECTING. The file is a one-pixel PNG in a temp directory
    (the panel only needs it to exist); both writes use the app's own store functions."""
    db = _bind(args.db)
    import base64
    import tempfile
    from app.core import db_booking
    s = json.loads(args.spec)
    sid = db_booking.create_booking_request(
        s["dni"], s["nombre"], s["apellido"], s.get("nacimiento", "1984-03-02"), s.get("contacto"),
        s["servicio_id"], s["centro"], solicitante_dni=s["dni"])
    path = os.path.join(tempfile.mkdtemp(prefix="e2e-orden-"), "orden.png")
    with open(path, "wb") as handle:
        handle.write(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="))
    db_booking.set_referral(sid, path, "image/png")
    db_booking.save_ai(sid, {"verdict": s.get("verdict", "ilegible")})
    _out({"request_id": sid})


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, func, *flags):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True)
        for flag in flags:
            p.add_argument("--" + flag)
        p.set_defaults(func=func)

    add("seed-operators", cmd_seed_operators, "spec")
    add("seed-patient", cmd_seed_patient, "spec")
    add("seed-visit", cmd_seed_visit, "spec")
    add("seed-interview", cmd_seed_interview, "spec")
    add("doctors", cmd_doctors)
    add("doctor-of-interview", cmd_doctor_of_interview, "interview")
    add("doctor-intake", cmd_doctor_intake, "doctor", "on")
    add("seed-dependent", cmd_seed_dependent, "spec")
    add("dependents", cmd_dependents, "dni")
    add("requests", cmd_requests, "dni")
    add("request", cmd_request, "id")
    add("interview-of", cmd_interview_of, "request")
    add("consultations", cmd_consultations, "interview")
    add("tick-reminders", cmd_tick_reminders, "now")
    add("consultation-contents", cmd_consultation_contents, "interview")
    add("doctor-windows", cmd_doctor_windows, "doctor")
    add("attach-referral-file", cmd_attach_referral_file, "request", "file", "mime")
    add("reject", cmd_reject, "id", "motivo")
    add("access-matrix", cmd_access_matrix)
    add("patient", cmd_patient, "dni")
    add("door", cmd_door, "doctor", "url", "token")
    add("seed-unreadable-referral", cmd_seed_unreadable_referral, "spec")

    args = ap.parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": base._redact(e)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
