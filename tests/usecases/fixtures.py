#!/usr/bin/env python3
"""fixtures.py — idempotent seed for the use-case browser suite.

Spec: the 46-case use-case matrix — this file
seeds exactly the "Fixtures" section of that matrix against the LOCAL dev /
preview database the suite's server (PORT=7700) runs on, and nothing else.

Writes go through core/db.py the way the retired seed_demo_cola.py did (direct
db writes + delete-own-rows-first idempotency), with two updates that script
predates: the schema Anglicization (its `solicitudes` table no longer exists
— seeded rows go through db_booking.create_booking_request/db_booking.approve) and the
catalog overlay (fixture doctors/services/centros are installed as
db_catalog.catalog_upsert rows, which win over the base pack JSON and are visible to
the running server within catalog.py's 3 s TTL — no server restart needed).

Deliberate deviations from the matrix's Fixtures paragraph, both required by
the case table itself:
  - dr-cierre's cierre is NOT preset: C7's Action is "Admin sets a Cierre
    total", and the cierres editor allows duplicates — presetting would make
    C7 produce two identical rows and fail its own Expected.
  - two helper entities beyond the named list: dr-serie (svc-serie's doctor;
    the matrix names the service but no doctor, and its slots must not come
    from another case's doctor) and svc-sin-agenda / svc-multi-a / svc-multi-b
    / svc-limite2 / svc-orden-target pages the case table drives (C4, E3/E4,
    D10, D3). Service→doctor isolation is by NEW centros (centro-a/centro-b)
    plus franja-level `servicio` tags, so no base-pack doctor ever bleeds
    into a fixture picker.

Credentials: the suite owns a dedicated admin operator `uc.suite` plus the
patient account; it must not reuse an installation owner's credentials. Both have
generated passwords written to artifacts/usecases/.creds.json (gitignored,
outside the committed tree) and backed up through the local credential-vault
CLI when available. No deployment login history belongs in this public fixture.

Usage:
    python3 tests/usecases/fixtures.py        # full reset + seed (idempotent)
    python3 tests/usecases/fixtures.py e2     # mid-run: E2's single approved
                                             # appointment on dra-sobre
"""

from __future__ import annotations

import json
import os
import re
import sys
import secrets
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ---- database resolution: the SAME database the 7700 server runs on -------
#
# The dev server is launched by ~/.claude/bin/human-rounds-preview-runner.py,
# which sets DATABASE_URL from a URL file. Mirror that resolution, then
# hard-refuse anything that is not loopback: this seeder must never reach a
# remote or production database.

_PREVIEW_URL_FILE = os.environ.get(
    "TURNOS_PREVIEW_DB_URL_FILE",
    os.path.expanduser("~/.claude/state/human-rounds-preview/database-url"),
)


def _resolve_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url and os.path.exists(_PREVIEW_URL_FILE):
        with open(_PREVIEW_URL_FILE, encoding="utf-8") as f:
            url = f.read().strip()
    if not url:
        raise SystemExit(
            "fixtures: no DATABASE_URL and no preview URL file — "
            "nothing to seed (this suite only targets the local dev DB).")
    m = re.match(r"postgres(ql)?://[^@]+@([^/:]+)(?::(\d+))?/(.+)", url)
    if not m:
        raise SystemExit("fixtures: unparseable DATABASE_URL")
    host = m.group(2)
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(f"fixtures: refusing non-loopback database host {host!r}")
    return url


_TEST_DIR = os.path.join(ROOT, "tests")
if _TEST_DIR not in sys.path:
    sys.path.insert(0, _TEST_DIR)

import _env  # noqa: E402  (tests/_env.py: this script is run on its own, tests/ is not on the path)

_env.process_set(DATABASE_URL=_resolve_database_url())
# Never let config fall through to a repo .env that points somewhere else.
_env.process_defaults(TURNOS_ENV_FILE="/dev/null")

from app.core import db  # noqa: E402  (import after DATABASE_URL is pinned)
from app.core import db_booking  # noqa: E402  (import after DATABASE_URL is pinned)
from app.core import db_accounts  # noqa: E402  (import after DATABASE_URL is pinned)
from app.core import db_operators  # noqa: E402  (import after DATABASE_URL is pinned)
from app.core import db_catalog  # noqa: E402  (import after DATABASE_URL is pinned)
from app.core import tz  # noqa: E402
from app.patient_web import catalog as _catalog  # noqa: E402

ART = tz.tzinfo()

# ---- the fixture universe --------------------------------------------------

MARCA = "@uc-demo.example.com"          # end-of-contact marker, reset key
CREDS_PATH = os.path.join(ROOT, "artifacts", "usecases", ".creds.json")

PACIENTE = dict(dni="30444001", nombre="Patricia", apellido="Caso",
                nacimiento="1990-03-14", sexo="F")
EXTRA_DNIS = ["30555001", "30555002", "30555003", "30555004", "30555005"]

STAFF = ["adm.mesa1", "med.sobre", "rep.jefatura", "adm.suplente", "uc.suite"]

CENTROS = [
    dict(slug="centro-a", name="Use Case Center A",
         address="Suite St. 100", phone="(011) 4000-1000",
         email="centro-a" + MARCA,
         description="Centro ficticio del suite de casos de uso (A).",
         jtbd="Los turnos de prueba del suite, lado A.",
         jobs=["Sacar un turno de prueba"], bookable=True),
    dict(slug="centro-b", name="Use Case Center B",
         address="Suite St. 200", phone="(011) 4000-2000",
         email="centro-b" + MARCA,
         description="Centro ficticio del suite de casos de uso (B).",
         jtbd="Los turnos de prueba del suite, lado B.",
         jobs=["Sacar un turno de prueba"], bookable=True),
]

# franja: L-V 09:00-12:00 cada 20 min = 9 slots/día (matrix header, group C)
def _franja(servicio, sobreturnos=0, desde="09:00", hasta="12:00", dias=(1, 2, 3, 4, 5)):
    fr = {"dias": list(dias), "desde": desde, "hasta": hasta, "cada_min": 20,
          "servicio": servicio,
          "cupos": {"web": 1, "reservado": 0, "sobreturnos": sobreturnos}}
    return fr


def _cluster_for(especialidad):
    """The SAME derivation the server does on every admin save
    (catalog._specialty_cluster): a fixture doctor's cluster must equal what
    the panel would write, or the service→doctor join in
    site_availability._appointments_for_service (cluster == cluster, centro ==
    centro) never matches and the wizard offers no slots."""
    cl = _catalog._specialty_cluster(especialidad)  # noqa: SLF001 (seed script)
    if not cl:
        raise SystemExit(f"fixtures: especialidad {especialidad!r} no mapea a "
                         "ningún cluster de especialidades.json")
    return cl


def _doctor(id_, nombre_pila, apellido, especialidad, franjas, extra=None):
    doc = {
        "id": id_, "nombre_pila": nombre_pila, "apellido": apellido,
        "nombre": f"{nombre_pila} {apellido}", "especialidad": especialidad,
        "cluster": _cluster_for(especialidad),
        "centro": "centro-a",
        "centros": [{"centro": "centro-a", "franjas": franjas, "excepciones": []}],
        "agenda": {"franjas": franjas, "excepciones": []},
    }
    if extra:
        doc.update(extra)
    return doc


DOCTORS = [
    _doctor("dra-con-agenda", "Constanza", "Agendi", "Medicina general",
            [_franja("svc-libre")]),
    _doctor("dr-sin-agenda", "Simón", "Sinagenda", "Medicina general", []),
    _doctor("dra-sobre", "Sonia", "Sobrera", "Ginecología y Obstetricia",
            [_franja("svc-sobre", sobreturnos=3)]),
    # dr-cierre: agenda only — the cierre itself is created by case C7
    _doctor("dr-cierre", "Ciro", "Cierre", "Pediatría", [_franja("svc-cierre")]),
    _doctor("dr-multi", "Milo", "Multipro", "Endocrinología",
            [_franja("svc-multi-a", dias=(1, 3, 5)), _franja("svc-multi-b", dias=(2, 4))]),
    _doctor("dr-unico", "Ulises", "Unico", "Cardiología", [_franja("svc-orden")]),
    _doctor("dr-limite2", "Lima", "Limita", "Psicología",
            [_franja("svc-limite2")], extra={"turnos_activos_max": 2}),
    _doctor("dr-serie", "Sergio", "Serie", "Kinesiología", [_franja("svc-serie")]),
]
# E3/E4: dr-multi sees patients at centro-b (both services, same centre)
for m in DOCTORS:
    if m["id"] == "dr-multi":
        m["centro"] = "centro-b"
        m["centros"] = [{"centro": "centro-b", "franjas": m["agenda"]["franjas"],
                         "excepciones": []}]

SERVICES = [
    dict(id="svc-libre", nombre="UC Turno libre", centro="centro-a",
         modo="solicitud", cluster="clinica",
         descripcion="Servicio ficticio del suite: turno sin orden.",
         cartel="Pedí tu turno acá y el centro te lo confirma.",
         categoria="especialidades", horario="Lunes a viernes de 9 a 12 h",
         palabras=["use case", "turno libre"]),
    dict(id="svc-orden", nombre="UC Turno con orden", centro="centro-a",
         modo="solicitud_con_orden", cluster="cardiologia",
         descripcion="Servicio ficticio del suite: turno con orden de derivación.",
         cartel="Subí tu orden y el centro te confirma el turno.",
         categoria="especialidades", horario="Lunes a viernes de 9 a 12 h",
         palabras=["use case", "orden"]),
    dict(id="svc-espontanea", nombre="UC Demanda espontánea", centro="centro-a",
         modo="walkin", cluster="clinica",
         descripcion="Servicio ficticio del suite: demanda espontánea.",
         cartel="Sin turno: vení directamente por orden de llegada.",
         categoria="especialidades", horario="Lunes a viernes de 8 a 13 h",
         palabras=["use case", "espontánea"]),
    dict(id="svc-espontanea-orden", nombre="UC Espontánea con orden", centro="centro-a",
         modo="walkin_con_orden", cluster="clinica",
         descripcion="Servicio ficticio del suite: espontánea con orden.",
         cartel="Sin turno: vení con tu orden por orden de llegada.",
         categoria="especialidades", horario="Lunes a viernes de 8 a 13 h",
         palabras=["use case", "espontánea con orden"]),
    dict(id="svc-serie", nombre="UC Turno en serie", centro="centro-a",
         modo="solicitud", cluster="trauma-kine", turnos_activos_max=3,
         descripcion="Servicio ficticio del suite: serie de turnos.",
         cartel="Pedí tu turno acá y el centro te lo confirma.",
         categoria="especialidades", horario="Lunes a viernes de 9 a 12 h",
         palabras=["use case", "serie"]),
    dict(id="svc-sobre", nombre="UC Con sobreturnos", centro="centro-a",
         modo="solicitud", cluster="gineco",
         descripcion="Servicio ficticio del suite: agenda con 3 sobreturnos.",
         cartel="Pedí tu turno acá y el centro te lo confirma.",
         categoria="especialidades", horario="Lunes a viernes de 9 a 12 h",
         palabras=["use case", "sobreturnos"]),
    dict(id="svc-cierre", nombre="UC Con cierre", centro="centro-a",
         modo="solicitud", cluster="pediatria",
         descripcion="Servicio ficticio del suite: agenda con cierre.",
         cartel="Pedí tu turno acá y el centro te lo confirma.",
         categoria="especialidades", horario="Lunes a viernes de 9 a 12 h",
         palabras=["use case", "cierre"]),
    dict(id="svc-multi-a", nombre="UC Multi A", centro="centro-b",
         modo="solicitud", cluster="endocrino",
         descripcion="Servicio ficticio del suite: multi A.",
         cartel="Pedí tu turno acá y el centro te lo confirma.",
         categoria="especialidades", horario="Lunes, miércoles y viernes de 9 a 12 h",
         palabras=["use case", "multi a"]),
    dict(id="svc-multi-b", nombre="UC Multi B", centro="centro-b",
         modo="solicitud", cluster="endocrino",
         descripcion="Servicio ficticio del suite: multi B.",
         cartel="Pedí tu turno acá y el centro te lo confirma.",
         categoria="especialidades", horario="Martes y jueves de 9 a 12 h",
         palabras=["use case", "multi b"]),
    dict(id="svc-limite2", nombre="UC Límite por médico", centro="centro-a",
         modo="solicitud", cluster="salud-mental",
         descripcion="Servicio ficticio del suite: límite 2 por médico.",
         cartel="Pedí tu turno acá y el centro te lo confirma.",
         categoria="especialidades", horario="Lunes a viernes de 9 a 12 h",
         palabras=["use case", "límite médico"]),
    dict(id="svc-sin-agenda", nombre="UC Sin agenda", centro="centro-a",
         modo="solicitud", cluster="clinica",
         descripcion="Servicio ficticio del suite: sin agenda cargada.",
         cartel="Pedí tu turno acá y el centro te lo confirma.",
         categoria="especialidades", palabras=["use case", "sin agenda"]),
]

SERVICE_IDS = [s["id"] for s in SERVICES]
DOCTOR_IDS = [d["id"] for d in DOCTORS]
CENTER_IDS = [c["slug"] for c in CENTROS]

DOCTOR_NAME = {d["id"]: d["nombre"] for d in DOCTORS}


# ---- dates (computed at seed time in the install timezone, never hard-coded)

def _next_weekday(after: date, weekdays=(1, 2, 3, 4, 5)) -> date:
    d = after
    while d.isoweekday() not in weekdays:
        d += timedelta(days=1)
    return d


def _art(date_: date, hh: int, mm: int = 0) -> datetime:
    return datetime(date_.year, date_.month, date_.day, hh, mm, tzinfo=ART)


# ---- credentials -----------------------------------------------------------

def _load_or_create_creds() -> dict:
    """Generated once, then reused — running the seed twice must leave the
    same state, and the runner needs the same passwords."""
    if os.path.exists(CREDS_PATH):
        with open(CREDS_PATH, encoding="utf-8") as f:
            try:
                creds = json.load(f)
            except ValueError:
                creds = {}
        if creds.get("admin_pass") and creds.get("patient_pass"):
            return creds
    creds = {
        "admin_user": "uc.suite",
        "admin_pass": "UC-" + secrets.token_urlsafe(9),
        "patient_dni": PACIENTE["dni"],
        "patient_pass": "UC-" + secrets.token_urlsafe(9),
    }
    os.makedirs(os.path.dirname(CREDS_PATH), exist_ok=True)
    with open(CREDS_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(creds, ensure_ascii=False, indent=2))
        os.chmod(CREDS_PATH, 0o600)
    _creds_to_vault(creds)
    return creds


def _creds_to_vault(creds: dict) -> None:
    """Best-effort, per the harness rule that new credentials land in
    credential vault at creation. Silence is fine — the local file is the
    source of truth for the suite."""
    import subprocess
    item = {
        "title": "Human Rounds — use-case suite (preview)",
        "category": "LOGIN",
        "fields": [
            {"id": "username", "type": "STRING", "purpose": "USERNAME",
             "value": creds["admin_user"]},
            {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD",
             "value": creds["admin_pass"]},
            {"label": "Patient identifier", "type": "STRING", "value": creds["patient_dni"]},
            {"label": "Patient password", "type": "CONCEALED", "value": creds["patient_pass"]},
        ],
    }
    try:
        # Never put generated credentials in process arguments. The local
        # operator may override the vault; the existing harness default stays.
        subprocess.run(
            ["op", "item", "create", "-", "--vault",
             os.environ.get("TURNOS_TEST_CREDENTIAL_VAULT", "AI"),
             "--url", "http://127.0.0.1:7700"],
            input=json.dumps(item), text=True, capture_output=True, timeout=20)
    except Exception:  # noqa: BLE001
        pass


# ---- reset (idempotency: delete our own rows, then re-create) --------------

def _delete_requests(cur, where, params):
    """DELETE FROM booking_requests WHERE <where>, children first: since Alembic
    0028 the database refuses to delete a request that still has history, holds,
    a waiting-list entry or an interview under it."""
    ids = "SELECT id FROM booking_requests WHERE " + where
    interviews = "SELECT id FROM interviews WHERE request_id IN (" + ids + ")"
    waits = "SELECT id FROM waitlist WHERE request_id IN (" + ids + ")"
    for sql in (
        "DELETE FROM consultations WHERE interview_id IN (" + interviews + ")",
        "UPDATE patient_health_profile SET source_interview_id = NULL "
        "WHERE source_interview_id IN (" + interviews + ")",
        "DELETE FROM interviews WHERE request_id IN (" + ids + ")",
        "DELETE FROM appointment_offers WHERE waitlist_id IN (" + waits + ")",
        "DELETE FROM waitlist WHERE request_id IN (" + ids + ")",
        "DELETE FROM attendance_events WHERE request_id IN (" + ids + ")",
        "DELETE FROM appointment_events WHERE request_id IN (" + ids + ")",
        "DELETE FROM slot_holds WHERE request_id IN (" + ids + ")",
        "DELETE FROM booking_requests WHERE " + where,
    ):
        cur.execute(sql, params)


def reset() -> None:
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001 (seed script)
        _delete_requests(
            cur,
            "service_id = ANY(%s) OR contact LIKE %s OR dni = ANY(%s) OR requester_dni = ANY(%s)",
            (SERVICE_IDS, "%" + MARCA,
             [PACIENTE["dni"]] + EXTRA_DNIS, [PACIENTE["dni"]] + EXTRA_DNIS))
        cur.execute("DELETE FROM slot_holds WHERE service_id = ANY(%s)", (SERVICE_IDS,))
        cur.execute("DELETE FROM operator_access WHERE username = ANY(%s)", (STAFF,))
        cur.execute("DELETE FROM operator_username_history WHERE operator_id IN "
                    "(SELECT id FROM operators WHERE username = ANY(%s))", (STAFF,))
        cur.execute("DELETE FROM operators WHERE username = ANY(%s)", (STAFF,))
        # B1/B4/B6/B8 assert the ROLE-default landing: a `nav.last_screen`
        # preference orphaned by the operator delete above would still steer
        # the re-created account's next login (admin_preferences is keyed by
        # username alone), so it goes in the same breath.
        cur.execute("DELETE FROM admin_preferences WHERE username = ANY(%s)", (STAFF,))
        dnis = [PACIENTE["dni"]] + EXTRA_DNIS
        for child, column in (("sessions", "dni"), ("reset_tokens", "dni"),
                              ("dependents", "adult_dni"), ("patient_lab_uploads", "uploaded_by_dni")):
            cur.execute("DELETE FROM " + child + " WHERE " + column + " = ANY(%s)", (dnis,))
        cur.execute("DELETE FROM users WHERE dni = ANY(%s)", (dnis,))
        cur.execute(
            "DELETE FROM catalog WHERE type = ANY(%s) AND id = ANY(%s)",
            (["servicios", "medicos", "centros"], SERVICE_IDS + DOCTOR_IDS + CENTER_IDS))
        conn.commit()
    print("  reset: booking_requests/slot_holds/operators/users/catalog del suite")


# ---- seed -------------------------------------------------------------------

def seed_catalog() -> None:
    for c in CENTROS:
        db_catalog.catalog_upsert("centros", c["slug"], c)
    for s in SERVICES:
        db_catalog.catalog_upsert("servicios", s["id"], s)
    for d in DOCTORS:
        db_catalog.catalog_upsert("medicos", d["id"], d)
    print(f"  catalog: {len(CENTROS)} centros, {len(SERVICES)} servicios, "
          f"{len(DOCTORS)} médicos (overlay)")


def seed_accounts(creds: dict) -> None:
    # Suite admin + patient: reset() already deleted both, so these are
    # always plain creates — the password in the creds file is the one that
    # works, run after run.
    db_operators.operator_create(creds["admin_user"], creds["admin_pass"], rol="admin")
    db_accounts.create_user(PACIENTE["dni"], PACIENTE["nombre"], PACIENTE["apellido"],
                   PACIENTE["nacimiento"], PACIENTE["sexo"],
                   "pato.caso" + MARCA, creds["patient_pass"],
                   email="pato.caso" + MARCA)
    print(f"  cuentas: admin {creds['admin_user']} + paciente {PACIENTE['dni']}")


def _approved(dni, nombre, apellido, nacimiento, sexo, servicio, centro,
              cuando, medico_id):
    """Pending request -> approved appointment (the seed_demo_cola write,
    through today's db.py functions instead of the retired `solicitudes`
    table)."""
    sid = db_booking.create_booking_request(
        dni, nombre, apellido, nacimiento, "e2e" + MARCA, servicio, centro,
        sexo=sexo, slot_medico_id=medico_id)
    db_booking.approve(sid, cuando, servicio_id=servicio,
               medico_asignado=DOCTOR_NAME[medico_id],
               medico_asignado_id=medico_id, resuelta_por="uc-fixtures")
    return sid


def _pending(dni, nombre, apellido, nacimiento, sexo, servicio, centro, medico_id):
    sid = db_booking.create_booking_request(
        dni, nombre, apellido, nacimiento, "e2e" + MARCA, servicio, centro,
        sexo=sexo, slot_medico_id=medico_id)
    db_booking.assign_pending_booking_request(sid, DOCTOR_NAME[medico_id], medico_id)
    return sid


def seed_appointments() -> None:
    hoy = datetime.now(ART).date()
    sem = _next_weekday(hoy + timedelta(days=7))     # next week, safely future
    ids = []
    # E5: today's queue holds approved appointments at centro-a AND centro-b
    ids.append(_approved("30555001", "Vera", "Cola", "1980-05-01", "F",
                         "svc-libre", "centro-a", _art(hoy, 8, 0),
                         "dra-con-agenda"))
    ids.append(_approved("30555002", "Bruno", "Cola", "1975-09-02", "M",
                         "svc-multi-a", "centro-b", _art(hoy, 10, 0),
                         "dr-multi"))
    # D9: patient already holds 2 pendientes-aprobadas on svc-serie (limit 3)
    ids.append(_approved(PACIENTE["dni"], PACIENTE["nombre"], PACIENTE["apellido"],
                         PACIENTE["nacimiento"], PACIENTE["sexo"],
                         "svc-serie", "centro-a", _art(sem, 10, 0), "dr-serie"))
    ids.append(_approved(PACIENTE["dni"], PACIENTE["nombre"], PACIENTE["apellido"],
                         PACIENTE["nacimiento"], PACIENTE["sexo"],
                         "svc-serie", "centro-a", _art(sem, 11, 0), "dr-serie"))
    # D10: patient already holds 2 on svc-limite2 (doctor override 2)
    ids.append(_approved(PACIENTE["dni"], PACIENTE["nombre"], PACIENTE["apellido"],
                         PACIENTE["nacimiento"], PACIENTE["sexo"],
                         "svc-limite2", "centro-a", _art(sem, 10, 0), "dr-limite2"))
    ids.append(_approved(PACIENTE["dni"], PACIENTE["nombre"], PACIENTE["apellido"],
                         PACIENTE["nacimiento"], PACIENTE["sexo"],
                         "svc-limite2", "centro-a", _art(sem, 11, 0), "dr-limite2"))
    # E6: pending requests across doctors/centres
    ids.append(_pending("30555003", "Pepa", "Pendiente", "1988-01-02", "F",
                        "svc-orden", "centro-a", "dr-unico"))
    ids.append(_pending("30555004", "Pipo", "Pendiente", "1983-02-03", "M",
                        "svc-multi-b", "centro-b", "dr-multi"))
    print(f"  turnos: {len(ids)} filas (E5×2 hoy, D9×2, D10×2, E6×2 pendientes)")


def seed_e2() -> None:
    """E2's Given: dra-sobre with exactly ONE approved appointment, on a
    minute inside her franja so the overbooking mechanism is exercised."""
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        _delete_requests(cur, "service_id='svc-sobre' AND dni='30555005'", None)
        conn.commit()
    dia = _next_weekday(datetime.now(ART).date() + timedelta(days=1))
    sid = _approved("30555005", "Una", "Sobreturna", "1992-07-07", "F",
                    "svc-sobre", "centro-a", _art(dia, 10, 0), "dra-sobre")
    print(f"  e2: solicitud {sid} aprobada {dia.isoformat()} 10:00 (dra-sobre)")


def main(argv):
    modo = argv[1] if len(argv) > 1 else "all"
    if modo == "e2":
        seed_e2()
        return 0
    print("fixtures: reset + seed (dev DB, loopback)")
    creds = _load_or_create_creds()
    reset()
    seed_catalog()
    seed_accounts(creds)
    seed_appointments()
    print("fixtures: listo (el server ve el overlay en ~3 s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
