#!/usr/bin/env python3
"""tests/e2e/fixture.py — DB + seed lifecycle for the Tier-2 browser E2E
suite (tests/e2e/*.spec.mjs, run via `npm run e2e`, @playwright/test).

WHY A SEPARATE PYTHON SCRIPT (not reusing tests/_dbtest.py directly): the
e2e tests are @playwright/test files, not unittest — there is no setUpModule/
tearDownModule to hook. This mirrors _dbtest.py's throwaway-database
pattern (own DB per suite, dropped on teardown, same TEST_DATABASE_URL/
DATABASE_URL resolution via db_schema._load_env_file()) but exposes it as a CLI
the Node harness (tests/e2e/lib/harness.mjs) can shell out to, because the
seeding needs the app's own modules (db.py, catalog.py, intake_admin.py).

Usage:
    python3 fixture.py setup --db <name> [--intake] [--door-medico <id>]
    python3 fixture.py teardown --db <name>

`setup` always seeds ONE throwaway operator row -- with a completely empty
DB, setup.is_fresh_install() (zero operators AND zero catalog "config"
rows) 303-redirects every page to /setup, which would sink every e2e test
regardless of surface (see docs/architecture/setup-wizard.md). This row is
never used to log in; the admin e2e test authenticates as the env-owner
(TURNOS_ADMIN_USER/_PASS, set directly in the server subprocess's env by
the Node harness), which needs no DB row at all.

`--intake` additionally seeds an interview row (post_booking entry) and a
QR door token for `--door-medico` (REQUIRED with --intake, no default --
must be a doctor id that exists in whichever pack chain the server under
test is running; the gate itself runs PACK_CHAIN=global,example-town, never
a private/reference pack -- see docs/architecture/entrevista-previa.md) via
the exact functions the app itself uses (db_clinical.interview_create,
intake_admin.get_or_create_door_token) -- not hand-built rows, so a
schema/contract drift breaks this fixture the same way it would break the
real feature.

Tier 3 slices 1/2 (docs/roadmap/tier3-harness-design.md, Area 1) add four
more `setup` flags, same doctrine -- always seed through the app's own
functions, never a hand-built row:

  --totp       operator with 2FA enabled (db_operators.operator_create(totp_secret=)).
               The secret is base32; a currently-valid code is computed with
               the same maintained pyotp library production verifies with.
               `totp-code` (below) recomputes a
               fresh code on demand -- the one in `setup`'s own output is a
               convenience snapshot, not the only way to get one.
  --waitlist   two approved appointments (db_booking.create_booking_request +
               db_booking.approve) for the same service+center, one nearer
               ("waitlist_dni"/"waitlist_session" -- the one a test cancels)
               and one later that opted into waitlist.opt_in() for the
               nearer slot -- the exact real-app path booking_requests.py's
               patient cancel calls into (waitlist.release_slot).
  --queue      four TODAY-dated approved appointments for "Pacientes de hoy":
               three unassigned ones across the two real attendance states
               (unmarked / 'presente' / 'ausente', via db_booking.mark_attendance)
               plus one on the catalog doctor Sergio Ruiz (ruiz-sergio,
               cardiologia, several services) for the per-doctor "Crear
               turno" path.
  --account    one fresh patient account + an already-open session
               (db_accounts.create_user + db_accounts.create_session), no appointment yet --
               for a test that books ONE through the wizard itself.
  --requests   two FICTIONAL pending booking_requests (db_booking.create_booking_request,
               NOT approved) for the example-town electrocardiograma service --
               the exact rows the admin Solicitudes queue (/api/admin/requests,
               estado 'pendiente') reads. Hermetic: throwaway DNIs, throwaway
               fictional names, no patient account, no order image, no PII. Used
               by the Tier-5 /admin/solicitudes direct-screen e2e to prove two
               rows render, a detail opens, and one safe reject persists through
               the real /api/admin/requests/<id>/reject DB path.
  --doctor-patients  one fictional Juan González roster row, its consultation,
               and a linked doctor operator. Carlos Wu deliberately has no
               row, making the doctor-boundary assertion observable.
  --booking-first-paint  derives every real future slot of one public,
               synthetic no-order service through site_availability._agenda_slots,
               then approves a fictional request in each. The browser gate
               therefore reaches the real empty-availability fallback without
               inventing an API payload or modifying a catalog file.

`query-offers`/`query-request` (separate subcommands, not part of `setup`)
read back the real observable side effect of these flows -- through
db._connect()/_wire() (the app's own read path), never a hand-parsed raw
row -- so a test can assert "a waitlist offer/booking request actually
landed" without re-deriving the schema here.

Prints one JSON object to stdout on success; `{"error": "..."}` (exit 1) on
failure. Never prints a secret: the throwaway database's connection URL
(carries the Postgres password) is handed to the Node parent through a
short-lived mode-0600 file (`url_file` in the JSON is a PATH, not the DSN
itself) that the parent reads once and deletes -- see `_write_url_file()`."""

import argparse
import base64
import json
import os
import re
import stat
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
_TEST_DIR = os.path.join(ROOT, "tests")
if _TEST_DIR not in sys.path:
    sys.path.append(_TEST_DIR)

import _env  # noqa: E402  (tests/_env.py: the one place that writes the process environment)

# One-shot CLI process: these are its import-time settings, never undone.
_env.process_defaults(TURNOS_FAKE_AI="1")

_DB_NAME_RE = re.compile(r"^sana_turnos_test_[a-zA-Z0-9_]*$")

# Postgres DSNs carry the password inline (postgresql://user:pass@host/db).
# Any message that might embed one (a raised connection error, in
# particular) gets scrubbed before it reaches stdout/stderr -- this is a
# defense-in-depth net around the deliberate url_file handoff below, which
# is the actual fix for "fixture.py printed a credential-bearing DSN".
_DSN_RE = re.compile(r"(postgres(?:ql)?://[^:/@\s]*:)[^@\s]*(@)")


def _redact(msg):
    return _DSN_RE.sub(r"\1***\2", str(msg))


def _fail(msg):
    print(json.dumps({"error": _redact(msg)}))
    sys.exit(1)


def _require_test_db_name(db_name):
    """`db_name` gets interpolated straight into DROP/CREATE DATABASE (no
    placeholder support for identifiers in psycopg) — refuse anything that
    isn't obviously a throwaway e2e database, so a typo'd or malicious
    --db value can never touch a real database."""
    if not _DB_NAME_RE.match(db_name or ""):
        _fail(
            f"--db {db_name!r} inválido: debe matchear ^sana_turnos_test_ "
            "(nombre de base de datos de e2e descartable, nunca una base real)"
        )


def _urls_for(db_name):
    """(maintenance_url, test_db_url) — same resolution order as
    tests/_dbtest.py's PerModuleDb._base_url(): explicit env first, the
    checkout's own .env as fallback. No hardcoded DSN (no password in the
    repo)."""
    from app.core import db_schema
    env_file = db_schema._load_env_file()
    base = (os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
            or env_file.get("TEST_DATABASE_URL") or env_file.get("DATABASE_URL"))
    if not base:
        _fail("no TEST_DATABASE_URL/DATABASE_URL (env or .env) -- can't reach Postgres")
    p = urlsplit(base)
    maint = urlunsplit((p.scheme, p.netloc, "/postgres", p.query, p.fragment))
    test_url = urlunsplit((p.scheme, p.netloc, "/" + db_name, p.query, p.fragment))
    return maint, test_url


def _rand_b32_secret():
    """Throwaway TOTP secret, base32 -- same shape admin_users.py hands a
    real operator when turning on 2FA."""
    return base64.b32encode(os.urandom(10)).decode("ascii").rstrip("=")


def _totp_code_now(secret):
    """A currently-valid code from the maintained production dependency."""
    import pyotp
    return pyotp.TOTP(secret).now()


def _write_url_file(url):
    """Hands the credential-bearing DSN to the Node parent through a
    short-lived, mode-0600 file instead of stdout -- the parent (tests/e2e/
    lib/harness.mjs) reads it once and deletes it immediately. Only the
    file's PATH (not a secret) ever reaches the JSON printed to stdout."""
    fd, path = tempfile.mkstemp(prefix="sana_e2e_dburl_", suffix=".txt")
    os.close(fd)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 -- owner-only
    with open(path, "w", encoding="utf-8") as f:
        f.write(url)
    return path


def cmd_setup(args):
    _require_test_db_name(args.db)
    maint_url, test_url = _urls_for(args.db)
    try:
        import psycopg
        with psycopg.connect(maint_url, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS {args.db} WITH (FORCE)")
                cur.execute(f"CREATE DATABASE {args.db}")
    except Exception as e:  # noqa: BLE001
        _fail(f"Postgres no accesible: {e}")

    _env.process_set(DATABASE_URL=test_url)
    from app.core import db
    from app.core import db_booking
    from app.core import db_accounts
    from app.core import db_operators
    from app.core import db_clinical
    from app.core import db_schema
    db_schema._ensured.clear()

    url_file = _write_url_file(test_url)
    out = {"db": args.db, "url_file": url_file}
    try:
        if not args.fresh:
            db_operators.operator_create("e2e-fixture-op", password="unused-" + args.db, rol="admin")

        if args.intake:
            if not args.door_medico:
                os.remove(url_file)
                _fail("--intake requiere --door-medico <id existente en el pack activo>")

            # interviews.request_id is a foreign key (Alembic 0032): the entry page
            # needs a REAL request, not an invented id.
            door_dni = "30111621"
            door_request_id = db_booking.create_booking_request(
                door_dni, "Ines", "Puerta", "1980-01-01", "+5492254100011",
                "electrocardiograma-hospital", "sampleton-hospital", solicitante_dni=door_dni)
            # Out of every list the specs count: closed and old (the admin queue
            # shows pending requests and the recently resolved ones only).
            with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
                cur.execute("UPDATE booking_requests SET status='cancelada', "
                            "created = now() - interval '400 days' WHERE id=%s", (door_request_id,))
            interview = db_clinical.interview_create(entry_mode="post_booking", request_id=door_request_id)
            out["interview_request_id"] = door_request_id
            out["interview_token"] = interview["patient_token"]
            # El id suelto (sin doctor): /admin/consult lo usa para probar el
            # borde de titularidad -- un medico que abre la entrevista de OTRO
            # (o de nadie) recibe el mismo 404 que si no existiera
            # (consult_admin.guard_own_interview_or_404).
            out["interview_id"] = interview["id"]

            from app.modules.intake import intake_admin
            door_token, doc = intake_admin.get_or_create_door_token(args.door_medico)
            if door_token is None:
                os.remove(url_file)
                _fail(f"medico {args.door_medico!r} no está en el catálogo activo")
            out["door_token"] = door_token
            out["door_medico"] = args.door_medico
            # The interview is OFF by default (2026-10-21): this synthetic
            # doctor opts in explicitly, through the same override the panel
            # writes. Packs are never rewritten to manufacture consent.
            intake_admin.set_doctor_override(args.door_medico, True)

        if args.totp:
            secret = _rand_b32_secret()
            totp_user, totp_pass = "e2e-fixture-totp", "e2e-totp-throwaway-pass"
            db_operators.operator_create(totp_user, password=totp_pass, rol="admisionista",
                                totp_secret=secret)
            out["totp_user"] = totp_user
            out["totp_pass"] = totp_pass
            out["totp_secret"] = secret
            out["totp_code"] = _totp_code_now(secret)

        if args.waitlist:
            from datetime import datetime, timedelta, timezone
            from app.modules.turnos import waitlist as waitlist_mod
            servicio_id, centro_slug = "electrocardiograma-hospital", "sampleton-hospital"
            dni_a, dni_b = "30111100", "30111200"
            pass_a = "e2e-waitlist-throwaway-pass"
            # `near` is the slot patient A cancels; `later` is what patient B
            # already has -- the queue only offers a slot to a candidate
            # whose OWN appointment is later (waitlist._queue's own
            # criterion, "the released slot is an improvement").
            near = datetime.now(timezone.utc) + timedelta(days=2)
            later = datetime.now(timezone.utc) + timedelta(days=5)
            db_accounts.create_user(dni_a, "Ana", "Cancela", "1985-01-01", "F",
                            "+5492254100001", pass_a,
                            email="e2e-waitlist-a@example.invalid")
            out["waitlist_dni"] = dni_a
            out["waitlist_session"] = db_accounts.create_session(dni_a)
            sid_a = db_booking.create_booking_request(
                dni_a, "Ana", "Cancela", "1985-01-01", None,
                servicio_id, centro_slug, solicitante_dni=dni_a)
            db_booking.approve(sid_a, near, {"estado": "skip"})
            sid_b = db_booking.create_booking_request(
                dni_b, "Beto", "Espera", "1980-01-01", None,
                servicio_id, centro_slug, solicitante_dni=dni_b)
            db_booking.approve(sid_b, later, {"estado": "skip"})
            waitlist_mod.opt_in(sid_b, dni_b, "e2e-waitlist-b@example.invalid",
                                 servicio_id, centro_slug, later)

        if args.queue:
            from app.core import tz
            from datetime import datetime
            servicio_id, centro_slug = "electrocardiograma-hospital", "sampleton-hospital"
            hoy = datetime.now(tz.tzinfo())

            def _seed_cola(dni, apellido, hora, marca, medico=None, medico_id=None):
                sid = db_booking.create_booking_request(
                    dni, "Turno", apellido, "1990-01-01", None,
                    servicio_id, centro_slug, solicitante_dni=dni)
                db_booking.approve(sid, hoy.replace(hour=hora, minute=0, second=0, microsecond=0),
                           {"estado": "skip"},
                           medico_asignado=medico, medico_asignado_id=medico_id)
                if marca:
                    db_booking.mark_attendance(sid, marca, "e2e-fixture", exigir_no_futuro=False)
                return "Turno " + apellido

            # Three lab appointments under the stable "no assigned doctor"
            # key (what the attendance/SSE proofs click) plus ONE on a named
            # catalog doctor with several services -- the per-doctor "Crear
            # turno" path of /admin/cola, whose overlay scopes the service
            # typeahead to that doctor's cluster.
            out["queue_pendiente"] = _seed_cola("30111300", "Pendiente", 9, None)
            out["queue_presente"] = _seed_cola("30111301", "Presente", 10, "presente")
            out["queue_ausente"] = _seed_cola("30111302", "Ausente", 11, "ausente")
            out["queue_medico"] = {
                "nombre": "Sergio Ruiz", "id": "ruiz-sergio",
                "paciente": _seed_cola("30111303", "RuizDoctor", 12, None,
                                       medico="Sergio Ruiz", medico_id="ruiz-sergio"),
            }

        if args.queue_later:
            # One approved appointment still AHEAD today (the day queue offers «Cancelar
            # turno» only before its time): now + 4 h, capped at 23:59 the same day. Seeded
            # when the worker starts, so 30 min was less than a loaded run takes to reach the
            # step that cancels it; only the last minute before midnight ART is left out.
            from app.core import tz
            from datetime import datetime, timedelta
            ahora = datetime.now(tz.tzinfo())
            cuando = min(ahora + timedelta(hours=4),
                         ahora.replace(hour=23, minute=59, second=0, microsecond=0))
            sid = db_booking.create_booking_request(
                "30111304", "Turno", "Posterior", "1990-01-01", None,
                "electrocardiograma-hospital", "sampleton-hospital", solicitante_dni="30111304")
            db_booking.approve(sid, cuando.replace(second=0, microsecond=0), {"estado": "skip"})
            out["queue_later"] = "Turno Posterior"

        if args.hsi_changes:
            # Three turnos the desk's HSI extension loaded and the panel cancelled
            # since: one whose hour passed yesterday (HSI no longer changes it,
            # so it never reaches the panel of changes) and two still ahead.
            from app.core import tz
            from datetime import datetime, timedelta
            hoy = datetime.now(tz.tzinfo()).replace(second=0, microsecond=0)

            def _cancelado(dni, apellido, dias, hora, minuto):
                cuando = (hoy + timedelta(days=dias)).replace(hour=hora, minute=minuto)
                sid = db_booking.create_booking_request(
                    dni, "Turno", apellido, "1990-01-01", None,
                    "electrocardiograma-hospital", "sampleton-hospital", solicitante_dni=dni)
                db_booking.approve(sid, cuando, {"estado": "skip"})
                db_booking.cancel(sid, actor="e2e-fixture")
                return {"id": sid, "paciente": "Turno " + apellido,
                        "dia": cuando.strftime("%Y-%m-%d"), "hora": cuando.strftime("%H:%M")}

            out["hsi_changes"] = {
                "pasado": _cancelado("30111305", "Pasado", -1, 10, 20),
                "rechazado": _cancelado("30111306", "Rechazado", 3, 10, 0),
                "siguiente": _cancelado("30111307", "Siguiente", 4, 11, 0),
            }

        if args.account:
            dni = "30111400"
            db_accounts.create_user(dni, "Carla", "Reserva", "1990-06-15", "F",
                            "+5492254100002", "e2e-account-throwaway-pass",
                            email="e2e-account-c@example.invalid")
            out["account_dni"] = dni
            out["account_session"] = db_accounts.create_session(dni)

        if args.booking_first_paint:
            # The normal no-order schedules in packs/example-town are already
            # real synthetic availability. This additional fixture is only
            # the complementary empty case: fill one whole service via the
            # same approved-request path production uses, so slot_holds takes
            # the exact slots out of the public API response.
            from datetime import date, datetime, time
            from app.core import tz
            from app.patient_web import site_availability, site_catalog
            servicio_id, centro_slug = "odontopediatria-maternity", "maternity-center"
            servicio = site_catalog._service_by_id(servicio_id)
            slots = site_availability._agenda_slots((servicio or {}).get("agenda") or {})
            if not servicio or not slots:
                raise RuntimeError("booking-first-paint fixture requires populated synthetic odontopediatria agenda")
            for index, slot in enumerate(slots):
                dni = str(30992000 + index)
                request_id = db_booking.create_booking_request(
                    dni, "Fixture", "SlotLleno", "2016-01-01", None,
                    servicio_id, centro_slug, solicitante_dni=dni,
                )
                local_dt = datetime.combine(
                    date.fromisoformat(slot["dia"]), time.fromisoformat(slot["hora"]), tz.tzinfo(),
                )
                db_booking.approve(request_id, local_dt, {"estado": "e2e-booking-first-paint"})
            # This process can have populated slot_holds while deriving the
            # schedule. The browser server starts separately, but invalidate
            # here too so a future in-process fixture never sees stale slots.
            from app.modules.turnos import slot_holds
            slot_holds.invalidate()
            out["booking_empty_service"] = servicio_id
            out["booking_empty_slots"] = len(slots)

        if args.requests:
            # Two FICTIONAL pending requests -- the rows the Solicitudes queue
            # (/api/admin/requests, estado 'pendiente') reads. Same service/
            # center ids the --queue fixture already proved against the gate's
            # example-town pack. NOT approved (a pending request is what the
            # queue is made of); no patient account, no order image, no PII --
            # throwaway DNIs and fictional names, hermetic to this DB. The id
            # this returns is what /api/admin/requests/<id>/reject mutates, so
            # a browser-driven reject can be proven against the real DB row.
            servicio_id, centro_slug = "electrocardiograma-hospital", "sampleton-hospital"
            out["requests"] = []
            seeded = [("30111510", "Pilar", "Pendiente"), ("30111511", "Tomas", "Pendiente")]
            # Past ten (a queue long enough to scroll, TASK-53) the names
            # are numbered instead of lettered.
            seeded += [(str(30111520 + n), "Audit" + ("ABCDEFGHIJ"[n] if n < 10 else str(n)), "Pendiente")
                       for n in range(max(0, args.requests - 2))]
            for dni, nombre, apellido in seeded[:args.requests]:
                sid = db_booking.create_booking_request(
                    dni, nombre, apellido, "1990-01-01", None,
                    servicio_id, centro_slug, solicitante_dni=dni)
                out["requests"].append({"id": sid, "dni": dni,
                                        "nombre": f"{nombre} {apellido}"})

        if args.doctor_patients:
            from datetime import datetime, timedelta, timezone
            doctor_id = "gonzalez-juan"
            dni = "30111620"
            request_id = db_booking.create_booking_request(
                dni, "Paula", "Roster", "1980-01-01", "+5492254100010",
                "electrocardiograma-hospital", "sampleton-hospital", solicitante_dni=dni)
            db_booking.approve(
                request_id, datetime.now(timezone.utc) + timedelta(days=2),
                medico_asignado_id=doctor_id, resuelta_por="e2e-fixture",
            )
            interview = db_clinical.interview_create(
                doctor_id=doctor_id, entry_mode="post_booking", request_id=request_id,
                dni=dni, entered_name="Paula", entered_surname="Roster", matched=True,
            )
            db_clinical.interview_update_analysis(
                interview["id"], "VERDE", "Synthetic roster consultation.",
                {}, [], [], [], "fake-model",
            )
            db_clinical.interview_set_status(interview["id"], "done")
            # This doctor's agenda shows the interview, so it opted in
            # explicitly (the interview is OFF by default since 2026-10-21).
            from app.modules.intake import intake_admin
            intake_admin.set_doctor_override(doctor_id, True)
            user, password = "e2e-doctor-juan", "e2e-doctor-juan-pass"
            db_operators.operator_create(user, password=password, rol="medico", medico_id=doctor_id)
            out["doctor_patients"] = {
                "juan_id": doctor_id,
                "carlos_id": "wu-carlos",
                "nombre": "Paula Roster",
                "consultation_id": interview["id"],
                "medico_user": user,
                "medico_pass": password,
            }
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        try:
            os.remove(url_file)
        except OSError:
            pass
        _fail(f"seed falló: {e}")

    print(json.dumps(out))


def cmd_teardown(args):
    _require_test_db_name(args.db)
    maint_url, _ = _urls_for(args.db)
    try:
        import psycopg
        with psycopg.connect(maint_url, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS {args.db} WITH (FORCE)")
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"warn": _redact(e)}))
        return
    print(json.dumps({"ok": True}))


def cmd_totp_code(args):
    """A fresh 6-digit code for a secret `setup --totp` already returned --
    called right before the browser types it, so a slow server boot between
    `setup` and the login click can never race the code's 30s window."""
    print(json.dumps({"code": _totp_code_now(args.secret)}))


def cmd_query_offers(args):
    """Read-only: `appointment_offers` created recently, grouped by status,
    through db_reports.reports_appointment_offers() (the app's own read path) --
    the observable side effect of waitlist.release_slot() a cancel test
    asserts against, per docs/roadmap/tier3-harness-design.md Area 1."""
    _require_test_db_name(args.db)
    _maint, test_url = _urls_for(args.db)
    _env.process_set(DATABASE_URL=test_url)
    from app.core import db_reports
    print(json.dumps({"ofertas": db_reports.reports_appointment_offers(dias=1)}))


def cmd_mirror_hsi_busy(args):
    """Seed (or `--clear`) ONE mirrored HSI appointment on the first open slot of a
    service whose doctor the catalog can resolve by name -- the row the HSI sync
    would write for a turno created straight in HSI. Prints the hour taken."""
    _require_test_db_name(args.db)
    _maint, test_url = _urls_for(args.db)
    _env.process_set(DATABASE_URL=test_url)
    from datetime import datetime
    import sqlalchemy as sa
    from app.core import db, db_hsi, sqlcore, tz
    from app.core import schema_model as sm
    if args.clear:
        t = sm.hsi_appointments
        with db._connect() as conn, conn.cursor() as cur:
            sqlcore.run(cur, sa.delete(t).where(t.c.hsi_appointment_id == "e2e-hsi-busy"))
        print(json.dumps({"cleared": True}))
        return
    from app.modules.turnos import hsi_busy, schedule_policy
    from app.patient_web import catalog, site_availability, site_catalog
    servicio = site_catalog._service_by_id(args.service)
    slots = site_availability._agenda_slots((servicio or {}).get("agenda") or {})
    for slot in slots:
        for medico in catalog.doctors():
            if schedule_policy.belongs(medico, servicio) and hsi_busy._in_window(
                    medico, servicio, slot["dia"], slot["hora"]):
                name = medico.get("nombre") or medico.get("name")
                when = datetime.fromisoformat(slot["dia"] + "T" + slot["hora"]).replace(tzinfo=tz.tzinfo())
                db_hsi.hsi_mirror_schedule("e2e-diary", "e2e-prof", {"professional_name": name})
                db_hsi.hsi_mirror_appointment("e2e-hsi-busy", "e2e-diary", None, {
                    "id": "e2e-hsi-busy", "state": "scheduled", "scheduled_at": when.isoformat()})
                print(json.dumps({"servicio_id": args.service, "dia": slot["dia"],
                                  "hora": slot["hora"], "medico": name}))
                return
    _fail("no slot with a resolvable doctor for " + args.service)


def cmd_set_enabled_modules(args):
    """Tier 5 slice 4: writes ENABLED_MODULES straight to the SAME
    `catalogo` config row `config.guardar()`/`app_modules.enable()`/
    `disable()` already use -- lets an e2e test flip a module live
    against the running server subprocess without a restart, proving the
    module-contract "disable -> 404 + nav gone" property through a real
    browser instead of only through test_admin_screen_contract.py's
    in-process TestClient. `--value ""` restores the real default (unset
    -> every discovered module enabled, see app_modules.enabled_ids()).
    The running server picks this up on its own `config.py` cache TTL
    (3s, see config.py's `_TTL`) -- no signal, no restart, same "no
    restart needed" property test_module_runtime.py already proves for
    the legacy path and test_module_contract_asgi.py proves for the ASGI
    one."""
    _require_test_db_name(args.db)
    _maint, test_url = _urls_for(args.db)
    _env.process_set(DATABASE_URL=test_url)
    from app.core import config
    config.guardar({"ENABLED_MODULES": args.value or None})
    print(json.dumps({"ok": True}))


def cmd_query_request(args):
    """Read-only: the most recent booking_requests row for `--dni`, through
    db._connect()/_wire() (the app's own row-shape translation) -- proves a
    real row landed after a browser-driven /api/requests submit, without
    hand-parsing the schema here."""
    _require_test_db_name(args.db)
    _maint, test_url = _urls_for(args.db)
    _env.process_set(DATABASE_URL=test_url)
    from app.core import db
    from app.core import db_wire
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM booking_requests WHERE dni=%s ORDER BY id DESC LIMIT 1",
                    (args.dni,))
        row = db_wire._wire(cur.fetchone(), "booking_requests")
    if not row:
        print(json.dumps({"found": False}))
        return
    ai = row.get("ai_json") or {}
    print(json.dumps({
        "found": True,
        "estado": row.get("estado"),
        "servicio_id": row.get("servicio_id"),
        "ai_verdict": ai.get("verdict"),
        "contacto": row.get("contacto"),
    }))


def cmd_query_nota(args):
    """Read-only: the most recent nota-backed booking_requests row for
    `--nota` (nombre = the note the front desk typed, dni NULL), through
    db._connect()/_wire() (the app's own row-shape translation) -- proves a
    staff walk-in created through the real Crear turno overlay landed,
    without hand-parsing the schema here."""
    _require_test_db_name(args.db)
    _maint, test_url = _urls_for(args.db)
    _env.process_set(DATABASE_URL=test_url)
    from app.core import db
    from app.core import db_wire
    with db._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM booking_requests WHERE dni IS NULL AND first_name=%s"
                    " ORDER BY id DESC LIMIT 1", (args.nota,))
        row = db_wire._wire(cur.fetchone(), "booking_requests")
    if not row:
        print(json.dumps({"found": False}))
        return
    print(json.dumps({
        "found": True,
        "estado": row.get("estado"),
        "servicio_id": row.get("servicio_id"),
        "nombre": row.get("first_name"),
    }))


def cmd_set_theme(args):
    """E2E twin of set-enabled-modules, for the ONE Pinamar-only screen:
    writes the active theme through theme.save() -- the app's own function,
    the same `catalogo` (tipo="config", id="tema") row the admin Appearance
    screen writes -- so /admin/plan's "exists only when theme == pinamar"
    route can be proven through a real browser against the live server
    subprocess. The running server caches theme.current() for ~3s (core/
    theme.py's _TTL); callers must wait out (or poll past) that TTL, same
    as set-enabled-modules does for config.py's."""
    _require_test_db_name(args.db)
    _maint, test_url = _urls_for(args.db)
    _env.process_set(DATABASE_URL=test_url)
    from app.core import theme
    print(json.dumps({"ok": True, "tema": theme.save(args.value)}))


def cmd_query_user(args):
    """Read-only: the usuarios row for `--dni` via db_accounts.get_user() (the app's
    own read path, already stripped of pass_hash) -- proves a patient created
    through /admin/pacientes' real POST /api/admin/patients actually landed,
    without hand-parsing the schema here. Prints a safe subset only."""
    _require_test_db_name(args.db)
    _maint, test_url = _urls_for(args.db)
    _env.process_set(DATABASE_URL=test_url)
    from app.core import db_accounts
    u = db_accounts.get_user(args.dni)
    if not u:
        print(json.dumps({"found": False}))
        return
    print(json.dumps({
        "found": True,
        "dni": u.get("dni"),
        "nombre": u.get("nombre"),
        "apellido": u.get("apellido"),
        "activo": u.get("activo"),
    }))


def cmd_query_consultation(args):
    """Read-only: the latest consultations row for `--interview` via
    db_clinical.consultation_latest_for_interview() -- proves a consultation confirmed
    through /admin/consult's real POST .../confirm persisted, and that the
    receta/orden tokens exist. Tokens themselves are never printed (booleans
    only); the notes and the diagnosis count are the observable payload."""
    _require_test_db_name(args.db)
    _maint, test_url = _urls_for(args.db)
    _env.process_set(DATABASE_URL=test_url)
    from app.core import db_clinical
    row = db_clinical.consultation_latest_for_interview(args.interview)
    if not row:
        print(json.dumps({"found": False}))
        return
    diag = row.get("diagnoses")
    print(json.dumps({
        "found": True,
        "id": row.get("id"),
        "doctor_id": row.get("doctor_id"),
        "notas": row.get("notes"),
        "diagnosticos_n": len(diag) if isinstance(diag, list) else None,
        "receta": bool(row.get("receta_token")),
        "orden": bool(row.get("orden_token")),
    }))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup")
    s.add_argument("--db", required=True)
    s.add_argument("--intake", action="store_true")
    # No default identifies a real deployment's doctor -- the gate seeds
    # against the synthetic packs/example-town pack (see
    # tests/e2e/lib/harness.mjs's PACK_CHAIN), so callers that want the
    # --intake fixture MUST pass a doctor id that exists in whichever pack
    # chain the server under test is actually running.
    s.add_argument("--door-medico", required=False, default=None)
    s.add_argument("--totp", action="store_true")
    s.add_argument("--waitlist", action="store_true")
    s.add_argument("--queue", action="store_true")
    s.add_argument("--queue-later", action="store_true")
    s.add_argument("--hsi-changes", action="store_true")
    s.add_argument("--account", action="store_true")
    # Bare --requests seeds the historic two; --requests N seeds N (the form audit
    # spends one pending request per desk action).
    s.add_argument("--requests", nargs="?", type=int, const=2, default=0)
    s.add_argument("--doctor-patients", action="store_true")
    s.add_argument("--booking-first-paint", action="store_true")
    # A genuinely unconfigured installation: no operator, so the first-run wizard (/setup) is open.
    s.add_argument("--fresh", action="store_true")
    s.set_defaults(func=cmd_setup)

    t = sub.add_parser("teardown")
    t.add_argument("--db", required=True)
    t.set_defaults(func=cmd_teardown)

    sem = sub.add_parser("set-enabled-modules")
    sem.add_argument("--db", required=True)
    sem.add_argument("--value", required=False, default="")
    sem.set_defaults(func=cmd_set_enabled_modules)

    tc = sub.add_parser("totp-code")
    tc.add_argument("--secret", required=True)
    tc.set_defaults(func=cmd_totp_code)

    qo = sub.add_parser("query-offers")
    qo.add_argument("--db", required=True)
    qo.set_defaults(func=cmd_query_offers)

    mh = sub.add_parser("mirror-hsi-busy")
    mh.add_argument("--db", required=True)
    mh.add_argument("--service", default="medicina-general-riverside-clinic")
    mh.add_argument("--clear", action="store_true")
    mh.set_defaults(func=cmd_mirror_hsi_busy)

    qr = sub.add_parser("query-request")
    qr.add_argument("--db", required=True)
    qr.add_argument("--dni", required=True)
    qr.set_defaults(func=cmd_query_request)

    st = sub.add_parser("set-theme")
    st.add_argument("--db", required=True)
    st.add_argument("--value", required=True)
    st.set_defaults(func=cmd_set_theme)

    qu = sub.add_parser("query-user")
    qu.add_argument("--db", required=True)
    qu.add_argument("--dni", required=True)
    qu.set_defaults(func=cmd_query_user)

    qn = sub.add_parser("query-nota")
    qn.add_argument("--db", required=True)
    qn.add_argument("--nota", required=True)
    qn.set_defaults(func=cmd_query_nota)

    qc = sub.add_parser("query-consultation")
    qc.add_argument("--db", required=True)
    qc.add_argument("--interview", required=True)
    qc.set_defaults(func=cmd_query_consultation)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
