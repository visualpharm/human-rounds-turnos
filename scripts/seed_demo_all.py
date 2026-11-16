#!/usr/bin/env python3
"""scripts/seed_demo_all.py — realistic fictional data for the public demo
installation (demo.humanrounds.org), importable by `manage.py demo seed|reset`.

Composes the ideas of the older, narrower seed scripts against the CURRENT
schema (booking_requests' columns were Anglicized after those scripts were
written — `solicitudes`/`estado`/`turno_dt` are gone; the table is
`booking_requests` with `status`/`appointment_at`, see db.py's `_SCHEMA_SQL`):
  - seed_demo_cola.py's idea: a few approved appointments TODAY (some already
    marked "presente") so «Pacientes de hoy» has something to show, plus a
    doctor login to demo the médico view.
  - a NEW generator: ~180 booking requests spread over the last 90 days in a
    realistic state mix (approved/rejected/cancelled/pending), a handful of
    waitlist rows, and a run of patient searches — so Reportes
    (recupero/estadística/capacidad) reads as a real installation instead of
    an empty one. Most of these rows also carry one of the 15 sample order
    images from `packs/example-town/ordenes-demo/` (see that dir's README)
    with a hand-authored `ai_json` matching the image's expected verdict —
    so the admin queue/history shows real, varied AI reads (clean approvals,
    dudosa handwriting/no-stamp cases, an outright wrong document, and two
    foreign-jurisdiction rejections) instead of empty referral cards.
  - a handful of registered patient accounts (db_accounts.create_user) so the admin
    "Pacientes" list isn't empty either.

Sampleton (packs/example-town) grew to ~50 doctors/~70 services 2026-08
(scripts/generate_example_town_catalog.py). This script doesn't seed a
request against every one of them — `_SERVICIOS` below picks ONE
representative bookable (solicitud/solicitud_con_orden) service per
cluster that has a matching doctor at the same center, so the history/queue
read as a real spread of specialties without hand-authoring 45 near-
identical booking profiles. The "pick between several doctors for the SAME
service" angle from seed_demo_solicitudes_multi.py still doesn't apply here
(that fixture belonged to Pinamar's real catalog) — each entry below is
exactly one service, one doctor.

Hard guard: every public entry point (`seed`, `reset`) no-ops with a clear
message unless `demo_installation.enabled()` — pointing this at the real
Pinamar database by mistake (wrong DATABASE_URL) does nothing instead of
wiping real patients.

Marker: every row this script creates is recognizable —
  - booking_requests / users / waitlist: `contact`/`email` ends with
    "@demo.humanrounds.org"
  - searches: `source = "demo-seed"`
`seed()` deletes by that marker before recreating (idempotent: running it
twice yields the exact same rows, since the generator is deterministic, no
`random`). `reset()` instead wipes the tables outright (including whatever a
public visitor typed) and then calls `seed()`.

Run directly:  python3 scripts/seed_demo_all.py [seed|reset]
(same effect as `python3 manage.py demo seed|reset` — this module is what
that subcommand imports.)
"""
import os
import sys
import unicodedata
import zlib
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import shutil

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as _pg_insert  # noqa: E402

from app.core import db  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.core import schema_model as sm  # noqa: E402
from app.core import sqlcore  # noqa: E402
from app.core import tz  # noqa: E402
from app.ops import demo_installation  # noqa: E402
from app.core import config  # noqa: E402
from app.patient_web import doctor_photos  # noqa: E402
from app.cuentas import account_photo  # noqa: E402

ART = tz.tzinfo()
MARCA = "@demo.humanrounds.org"
SEARCH_SOURCE = "demo-seed"
# Published demo credentials are intentionally non-secret and reset with the demo.
PUBLIC_PATIENT_LOGIN = "DemoPaciente2026"
PUBLIC_DOCTOR_LOGIN = "DemoGonzalez2026"

# Sampleton's own catalog (packs/example-town) — NOT Pinamar's. One
# representative appointment-mode service (solicitud/solicitud_con_orden)
# per cluster that has a doctor of that same cluster at that same center —
# "grupos" and "vacunas-prevencion" have no such pairing (every service in
# those two clusters is walk-in only) so they're not represented here.
# Regenerate this list after re-running generate_example_town_catalog.py by
# picking, per cluster, `next(s for s in servicios if s['cluster']==cl and
# s['modo'] in ("solicitud","solicitud_con_orden"))` and the doctor with the
# same `cluster`+`centro`.
_SERVICIOS = [
    ("cardiologia-sampleton-hospital", "sampleton-hospital", "Sergio Ruiz", "ruiz-sergio"),
    ("medicina-general-riverside-clinic", "riverside-clinic", "Juan González", "gonzalez-juan"),
    ("ecografia-general-anexo", "diagnostic-center", "Mónica Herrero", "herrero-monica"),
    ("nutricion-riverside-clinic", "riverside-clinic", "Hernán Benítez", "benitez-hernan"),
    ("ginecologia-obstetricia-maternity", "maternity-center", "Emiliano Molina", "molina-emiliano"),
    ("odontologia-riverside-clinic", "riverside-clinic", "Mariana Núñez", "nunez-mariana"),
    ("otorrinolaringologia-hospital", "sampleton-hospital", "Julieta Cáceres", "caceres-julieta"),
    ("dermatologia-hospital", "sampleton-hospital", "Agustina Conti", "conti-agustina"),
    ("pediatria-maternity", "maternity-center", "Pablo Sánchez", "sanchez-pablo"),
    ("psicologia-riverside-clinic", "riverside-clinic", "Carolina Villalba", "villalba-carolina"),
    ("traumatologia-hospital", "sampleton-hospital", "Ana Rojas", "rojas-ana"),
]

_MOTIVOS_RECHAZO = [
    "No corresponde a este centro de salud",
    "La orden médica está vencida",
    "Falta la firma o el sello del profesional",
]

# Realistic Argentine coastal-town patients (the product owner, 2026-08-01: the old list
# read as a "United Nations sampler" — Chen, Priya, RunningWater, Zanele,
# Watanabe — not a real municipality). Spanish and Italian surnames
# dominate (~55% + ~24%), plus the genuine minority communities actually
# present on the Buenos Aires coast: Syrio-Libanesa ("turcos", one of the
# country's largest historic immigrant communities), the Volga-German and
# Danish colonies of the Atlantic coast/Tandilia area, and Basque and
# Jewish surnames. Two parallel, index-zipped lists (same mechanism as
# before, same discipline as the doctor pool in
# scripts/generate_example_town_catalog.py) — no `random`.
_NOMBRES = [
    "Camila", "Rodrigo", "Sofía", "Diego", "Valentina", "Lucas", "Martina",
    "Marco", "Isabella", "Andrés", "Fernanda", "Miguel", "Agustina", "Thiago",
    "Julieta", "Nicolás", "Catalina", "Facundo", "Delfina", "Ignacio",
    "Renata", "Tomás", "Zoe", "Bruno", "Josefina", "Franco", "Guadalupe",
    "Maximiliano", "Milagros", "Leandro", "Antonella", "Damián", "Brenda",
    "Cristian", "Yamila", "Walter", "Melina", "Osvaldo", "Ayelén", "Norberto",
    "Griselda", "Raúl", "Norma", "Héctor", "Beatriz", "Rubén", "Alicia",
    "Omar", "Yolanda", "Eduardo", "Mariana", "Sergio", "Verónica", "Adrián",
    "Lucía", "Hernán", "Carolina", "Ezequiel", "Marina", "Federico", "Paula",
    "Matías", "Cecilia", "Emiliano", "Rocío", "Gonzalo", "Victoria",
    "Gustavo", "Silvia", "Ricardo", "Claudia", "Daniel", "Gabriela",
    "Alejandro", "Ana", "Pablo", "Laura", "Fernando", "Patricia", "Carlos",
    "María", "Jorge", "Florencia", "Roberto", "Daniela", "Luis", "Micaela",
    "Juan", "Noelia", "Martín", "Elena", "Francisco", "Marisol",
]
_APELLIDOS = [
    "Fernández", "Ibáñez", "González", "Rodríguez", "López", "Díaz",
    "Martínez", "Pérez", "García", "Sánchez", "Romero", "Sosa", "Torres",
    "Álvarez", "Ruiz", "Ramírez", "Flores", "Acosta", "Benítez", "Medina",
    "Herrera", "Suárez", "Aguirre", "Molina", "Ibarra", "Cabrera", "Rojas",
    "Domínguez", "Vega", "Bustos", "Correa", "Peralta", "Núñez", "Ledesma",
    "Maldonado", "Arias", "Cáceres", "Coronel", "Ferreyra", "Vera",
    "Villalba", "Quiroga", "Escobar", "Bravo", "Salas", "Campos", "Juárez",
    "Paz", "Ojeda", "Aguilar", "Guzmán", "Bianchi", "Ferraro", "Rossi",
    "Colombo", "Conti", "Bruno", "Marino", "Costa", "Greco", "Moretti",
    "Fabbri", "Lombardi", "Bertolini", "Rinaldi", "Ferrari", "Gallo",
    "Rizzo", "Testa", "Vitale", "Bianco", "Milano", "Farina", "Selim",
    "Hallal", "Nader", "Abdala", "Chaya", "Yazbek", "Kalil", "Asaad",
    "Schmidt", "Weiss", "Hoffmann", "Larsen", "Andersen", "Etchegoyen",
    "Larrañaga", "Cohen", "Levy", "Wagner", "Bassi", "Simón",
]
assert len(_NOMBRES) == len(_APELLIDOS) == 93
assert len(set(_NOMBRES)) == 93 and len(set(_APELLIDOS)) == 93


def _slug(text):
    """ASCII-only, lowercase, no punctuation — safe as an email local-part."""
    normal = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return "".join(c for c in normal.lower() if c.isalnum())


def _telefono(i):
    """A fictional local cell number in the canonical stored form
    (`accounts._norm_phone`). The account's «Teléfono» shows it; the email
    marker stays in the account's email (25/10: the demo showed an email in
    the phone field). WhatsApp from the demo only reaches the deployment's
    own test phones (`otp_send.notificacion_permitida`)."""
    return f"+549225441{i:04d}"


def _pacientes():
    """Deterministic list of ~93 fictional patients — same content every run
    (no `random`), so `seed()` run twice produces IDENTICAL rows, not just
    identical counts."""
    out = []
    hoy = datetime.now(ART)
    for i, (nombre, apellido) in enumerate(zip(_NOMBRES, _APELLIDOS)):
        dni = str(30000000 + i * 137)
        edad_anios = 5 + (i * 61) % 78  # 5..82
        anio = hoy.year - edad_anios
        mes = (i % 12) + 1
        dia = (i * 7) % 27 + 1
        sexo = "F" if i % 2 == 0 else "M"
        contacto = f"{_slug(nombre)}.{_slug(apellido)}{MARCA}"
        out.append(dict(
            dni=dni, nombre=nombre, apellido=apellido,
            nacimiento=date(anio, mes, dia), sexo=sexo,
            contacto=contacto, telefono=_telefono(i),
        ))
    return out


PACIENTES = _pacientes()

# One canonical walkthrough patient.  Her DNI is the curated Alepo/Marqui
# fixture key, so the patient account, longitudinal doctor roster and chart
# summary all tell one coherent story instead of demonstrating three
# unrelated records.
DEMO_PATIENT = dict(
    dni="99000001", nombre="Sofía", apellido="Herrera",
    nacimiento=date(1953, 8, 11), sexo="F",
    contacto="sofia.herrera@demo.humanrounds.org", telefono=_telefono(9999),
)


def _now():
    return datetime.now(ART)


# ---------------------------------------------------------------- guard ----

def _guard(nombre_comando):
    if demo_installation.enabled():
        return True
    print(f"manage.py demo {nombre_comando}: no-op — TURNOS_DEMO_MODE no está "
          "prendido (esto NUNCA debe correr contra una instalación real).")
    return False


def dia_cubierto(dia=None):
    """¿Los datos ficticios de la demo ya cubren ese día? (hoy por defecto).

    Coolify corre `manage.py demo seed` como post_deployment_command en
    CADA deploy — y el 2026-08-05 hubo 6 en una mañana. Sin este chequeo,
    cada uno de esos deploys volvía a borrar y recrear 41 cuentas, 180
    solicitudes históricas y 25 búsquedas mientras el contenedor ya estaba
    sirviendo tráfico real: ~11 s de escrituras a la base en cada redeploy,
    de las cuales nada era visible para un paciente (el día ya estaba
    sembrado). demo_refresh.py's propio daemon interno ya evitaba este
    trabajo con la misma lógica (sólo resiembra cuando cambia el día
    calendario) — este chequeo lo mueve ACÁ para que TODO llamador de
    seed(), Coolify incluido, quede protegido igual, no sólo el daemon.

    Cubre el día sólo la COLA de ese día (`cola_del_dia`): el sid del domingo
    deja el próximo turno de Sofía en el lunes, y con esa sola fila el lunes
    se daba por sembrado y la cola quedaba vacía (09.11)."""
    dia = dia or _now().date()
    br = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        sqlcore.run(cur, sa.select(sa.literal_column("1")).select_from(br).where(
            *cola_del_dia(dia),
        ).limit(sa.literal_column("1")))
        return cur.fetchone() is not None


def cola_del_dia(dia):
    """Las condiciones de «turno de la cola de ese día» de la demo: una fila
    marcada, aprobada, con turno ese día y que no es de la paciente-vitrina
    (su próximo turno puede caer en un día que el sid todavía no armó). La
    misma definición para `dia_cubierto` y `ops/demo_refresh_store`."""
    br = sm.booking_requests
    return (
        br.c.contact.like("%" + MARCA),
        br.c.status == "aprobada",
        br.c.dni.is_distinct_from(DEMO_PATIENT["dni"]),
        # the doctors' booked agendas (`_seed_agendas`) are not a day's queue
        br.c.contact.not_like("%" + MARCA_AGENDA),
        sa.cast(br.c.appointment_at, sa.Date) == dia,
    )


# ------------------------------------------------------------ borrado -------

def _delete_marked(cur):
    """Deletes what a previous seed created, CHILDREN FIRST: since Alembic 0032
    the database refuses to delete a parent (a request, an interview, a
    waiting-list entry, an account) that still has rows under it. Every
    selection below is read before its parent goes."""
    marca = "%" + MARCA
    dni = DEMO_PATIENT["dni"]
    br, iv, wl = sm.booking_requests, sm.interviews, sm.waitlist
    # The featured patient is one fictional person every visitor of
    # /account/demo-access signs in as: what a visitor booked or cancelled
    # carries the visitor's contact, not the marker, and would otherwise pile
    # up in her «Mis turnos» from one day to the next.
    de_la_demo = sa.or_(br.c.contact.like(marca), br.c.dni == dni, br.c.requester_dni == dni)
    requests = sa.select(br.c.id).where(de_la_demo).correlate(None)
    # `.correlate(None)`: these selections read the very table the DELETE removes from
    # (`DELETE FROM interviews WHERE id IN (SELECT id FROM interviews ...)`), and Core
    # would otherwise correlate the inner SELECT away and fail to render it.
    interviews = sa.select(iv.c.id).where(
        sa.or_(iv.c.dni == dni, iv.c.request_id.in_(requests))).correlate(None)
    waits = sa.select(wl.c.id).where(
        sa.or_(wl.c.email.like(marca), wl.c.request_id.in_(requests))).correlate(None)
    cm = sm.chat_messages
    sqlcore.run(cur, sa.delete(cm).where(
        cm.c.channel == sa.literal_column("'interview'"),
        cm.c["key"].in_(sa.select(iv.c.session_key).where(iv.c.id.in_(interviews)).correlate(None))))
    sqlcore.run(cur, sa.delete(sm.consultations).where(sm.consultations.c.interview_id.in_(interviews)))
    sqlcore.run(cur, sa.delete(sm.patient_summaries).where(sm.patient_summaries.c.dni == dni))
    phv = sm.patient_health_profile
    sqlcore.run(cur, sa.delete(phv).where(sa.or_(phv.c.dni == dni, phv.c.source_interview_id.in_(interviews))))
    sqlcore.run(cur, sa.delete(iv).where(iv.c.id.in_(interviews)))
    sqlcore.run(cur, sa.delete(sm.appointment_offers).where(sm.appointment_offers.c.waitlist_id.in_(waits)))
    sqlcore.run(cur, sa.delete(wl).where(wl.c.id.in_(waits)))
    for child in (sm.attendance_events, sm.appointment_events, sm.slot_holds):
        sqlcore.run(cur, sa.delete(child).where(child.c.request_id.in_(requests)))
    sqlcore.run(cur, sa.delete(br).where(de_la_demo))
    sqlcore.run(cur, sa.delete(sm.searches).where(sm.searches.c.source == SEARCH_SOURCE))
    ev = sm.analytics_events
    sqlcore.run(cur, sa.delete(ev).where(ev.c.jid.like(TRAFICO_PREFIJO + "%")))
    us = sm.users
    # Accounts carry the marker in their email (their «Teléfono» is a phone
    # number); `contact` still catches the rows of seeds before 25/10.
    marked = sa.or_(us.c.email.like(marca), us.c.contact.like(marca))
    for child, column in ((sm.sessions, "dni"), (sm.reset_tokens, "dni"),
                          (sm.dependents, "adult_dni"), (sm.patient_lab_uploads, "uploaded_by_dni")):
        sqlcore.run(cur, sa.delete(child).where(
            child.c[column].in_(sa.select(us.c.dni).where(marked))))
    sqlcore.run(cur, sa.delete(us).where(marked))


# Children before parents (see `_delete_marked`): the database refuses to
# delete a parent that still has rows under it.
_RESET_TABLES = (
    "consultations", "patient_summaries", "clinical_history_records",
    "patient_health_profile", "interviews",
    "appointment_offers", "waitlist", "attendance_events", "appointment_events",
    "slot_holds", "booking_requests", "searches", "chat_messages", "chat_handoffs",
    "sessions", "reset_tokens", "patient_lab_uploads", "dependents", "users",
)


def reset():
    """Wipes EVERY transactional table (including whatever a public visitor
    typed, not just this script's own marked rows) and reseeds. Hard no-op
    unless demo_installation.enabled()."""
    if not _guard("reset"):
        return
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001 (script de demo)
        for tabla in _RESET_TABLES:
            sqlcore.run(cur, sa.delete(sm.metadata.tables[tabla]))
        conn.commit()
    print("reset: tablas transaccionales vacías.")
    seed()


# -------------------------------------------------------------- seeders ----

# The regulars: the history (_historia_idx) brings them back again and again.
# Every fictional patient has an account (TASK-54, 26.10): today's queue
# deals the whole list across the agendas, and «Crear turno» finds a patient
# through their record — someone standing in today's queue without one was
# «Ningún paciente con ese dato».
_N_CUENTAS = 40

# Obras sociales/prepagas reales de Argentina — puramente para que Reportes
# (recupero de costos, SUMAR+) tenga volumen realista que agrupar; nada de
# esto es una afirmación sobre coberturas reales de nadie.
_OBRAS_SOCIALES = [
    ("OSDE", "101"), ("Swiss Medical", "102"), ("IOMA", "103"), ("PAMI", "104"),
    ("Sancor Salud", "105"), ("Unión Personal", "106"), ("OSDEPYM", "107"),
    ("Galeno", "108"), ("Medifé", "109"), ("OMINT", "110"),
]


def _cobertura_de(i):
    """Determinístico por índice — 1 de cada 4 cuentas queda SIN cobertura
    (alimenta SUMAR+, población exclusivamente pública); el resto tiene obra
    social (recupero de costos), alternando origen 'declarada'/'puco' (ocr_dni
    vs padrón SISA vía connectors/puco/puco.py) y una prepaga adicional cada 5ta cuenta."""
    if i % 4 == 3:
        return None
    nombre, rnos = _OBRAS_SOCIALES[i % len(_OBRAS_SOCIALES)]
    obra = {"nombre": nombre, "rnos": rnos, "origen": "puco" if i % 2 else "declarada"}
    cobertura = {"obra_social": obra}
    if i % 5 == 0:
        cobertura["prepaga"] = "Plan 210"
    return cobertura


_CALLES = [
    "Los Aromos", "Los Fresnos", "San Martín", "Belgrano", "Los Tilos",
    "Los Cedros", "Mitre", "Sarmiento", "Los Ceibos", "Rivadavia",
]


def _domicilio_de(i):
    return {
        "calle": _CALLES[i % len(_CALLES)], "numero": str(100 + i * 23 % 900),
        "localidad": "Sampleton", "provincia": "Buenos Aires", "cp": "7167",
        "texto": f"{_CALLES[i % len(_CALLES)]} {100 + i * 23 % 900}, Sampleton",
    }


def _seed_patient_accounts():
    """REGISTERED patient accounts (db_accounts.create_user) so admin "Pacientes"
    isn't empty — with insurance coverage + a domicile on most of them so
    Reportes (recupero de costos, SUMAR+) and the patient list itself read
    like a real installation, not a bare name/DNI row."""
    creadas = 0
    try:
        db_accounts.create_user(
            dni=DEMO_PATIENT["dni"], nombre=DEMO_PATIENT["nombre"],
            apellido=DEMO_PATIENT["apellido"], nacimiento=DEMO_PATIENT["nacimiento"],
            sexo=DEMO_PATIENT["sexo"], contacto=DEMO_PATIENT["telefono"],
            password=PUBLIC_PATIENT_LOGIN, email=DEMO_PATIENT["contacto"],
            cobertura={"obra_social": {"nombre": "IOMA", "rnos": "103",
                                         "origen": "puco"}},
            domicilio={"calle": "Los Aromos", "numero": "101",
                       "localidad": "Sampleton", "provincia": "Buenos Aires",
                       "cp": "7167", "texto": "Los Aromos 101, Sampleton"},
        )
        creadas += 1
    except Exception:  # noqa: BLE001 — idempotencia defensiva
        pass
    for i, pac in enumerate(PACIENTES):
        try:
            db_accounts.create_user(
                dni=pac["dni"], nombre=pac["nombre"], apellido=pac["apellido"],
                nacimiento=pac["nacimiento"], sexo=pac["sexo"],
                contacto=pac["telefono"], password=PUBLIC_PATIENT_LOGIN,
                email=pac["contacto"], cobertura=_cobertura_de(i),
                domicilio=_domicilio_de(i),
            )
            creadas += 1
        except Exception:  # noqa: BLE001 — ya existe (no debería, recién se borró) u otra falla
            pass
    # The order images' own holders are accounts too: their rows are booking
    # history, so «Pacientes» and the referral-reading card need a real person
    # behind each seeded order.
    for j, titular in enumerate(_ORDEN_HOLDERS_NUEVOS):
        try:
            db_accounts.create_user(
                dni=titular["dni"], nombre=titular["nombre"], apellido=titular["apellido"],
                nacimiento=titular["nacimiento"], sexo=titular["sexo"],
                contacto=titular["telefono"], password=PUBLIC_PATIENT_LOGIN,
                email=titular["contacto"], cobertura=_cobertura_de(j),
                domicilio=_domicilio_de(j),
            )
            creadas += 1
        except Exception:  # noqa: BLE001 — idempotencia defensiva
            pass
    _spread_account_dates()
    print(f"  {creadas} cuentas de paciente creadas")


# The daily reseed recreates every demo account, so all of them were «new
# today»: «Cuentas nuevas por día» drew one bar at «Hoy» over 29 empty days.
_CUENTAS_DIAS = 120


def _spread_account_dates():
    """Each seeded account gets a sign-up day of its own in the last
    _CUENTAS_DIAS days (deterministic by DNI)."""
    us = sm.users
    ahora = _now()
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        dnis = [r["dni"] for r in sqlcore.run(cur, sa.select(us.c.dni).where(
            us.c.email.like("%" + MARCA))).fetchall()]
        for dni in dnis:
            x = zlib.crc32(f"alta|{dni}".encode())
            cuando = ahora - timedelta(days=x % _CUENTAS_DIAS, hours=(x // _CUENTAS_DIAS) % 12)
            sqlcore.run(cur, sa.update(us).where(us.c.dni == dni).values(created=cuando))
        conn.commit()


def _seed_featured_patient(agendas=None, proximamente=None):
    """The end-to-end patient/doctor walkthrough for the public demo.

    Sofia has prior visits visible in her account, a near-term appointment
    with the demo doctor, a completed pre-consult interview, and the curated
    Alepo/Marqui history keyed by her DNI.  The chart summary is generated
    here once; the normal hourly pre-generation daemon keeps it fresh.

    Every visit sits on a slot of its doctor's agenda (`AgendaDemo`); the
    upcoming one is `_turno_destacado`'s, reserved before today's queue is
    dealt so the two never share a slot.
    """
    agendas = agendas or AgendaDemo()
    ahora = _now()
    proximamente = proximamente or _turno_destacado(agendas)
    pasadas = [
        ("medicina-general-riverside-clinic", "riverside-clinic",
         "Juan González", "gonzalez-juan",
         ahora - timedelta(days=420), "presente"),
        ("cardiologia-sampleton-hospital", "sampleton-hospital",
         "Sergio Ruiz", "ruiz-sergio",
         ahora - timedelta(days=250), "presente"),
        ("ecografia-general-anexo", "diagnostic-center",
         "Mónica Herrero", "herrero-monica",
         ahora - timedelta(days=120), "presente"),
    ]
    request_ids = []
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        for servicio, centro, medico, medico_id, cuando, asistencia in pasadas:
            cuando = agendas.turno(medico_id, cuando, hasta=ahora.date() - timedelta(days=1),
                                   servicio=servicio)
            rid = _insert_request(
                cur, DEMO_PATIENT, servicio, centro, cuando - timedelta(days=2),
                "aprobada", turno=cuando,
                medico=medico, medico_id=medico_id,
                asistencia=asistencia, asistencia_en=cuando,
                resuelto_en=cuando - timedelta(days=1),
            )
            request_ids.append(rid)
        upcoming_id = _insert_request(
            cur, DEMO_PATIENT, "medicina-general-riverside-clinic", "riverside-clinic",
            ahora - timedelta(days=1), "aprobada", turno=proximamente,
            medico="Juan González", medico_id="gonzalez-juan",
            resuelto_en=ahora - timedelta(hours=20),
        )
        # A SECOND future appointment, days out with another service, so
        # «Mis turnos» keeps a future approved appointment even after the
        # today-one is cancelled by a visitor (or the day ends): the demo
        # always has both «Cancelar turno» and «Al calendario» to show.
        # `en_agenda`, not `turno`: no slot from tomorrow on means no second
        # appointment, never one in the past.
        futuro = agendas.en_agenda("ruiz-sergio", ahora + timedelta(days=3),
                                   desde=ahora.date() + timedelta(days=1),
                                   servicio="cardiologia-sampleton-hospital")
        if futuro:
            _insert_request(
                cur, DEMO_PATIENT, "cardiologia-sampleton-hospital", "sampleton-hospital",
                ahora - timedelta(days=1), "aprobada", turno=futuro,
                medico="Sergio Ruiz", medico_id="ruiz-sergio",
                resuelto_en=ahora - timedelta(hours=22),
            )
        conn.commit()

    iv = db_clinical.interview_create(
        doctor_id="gonzalez-juan", entry_mode="post_booking",
        request_id=upcoming_id, dni=DEMO_PATIENT["dni"], matched=True,
    )
    transcript = [
        ("assistant", "¿Cuál es el motivo principal de la consulta?"),
        ("user", "Quiero controlar la diabetes y la presión. Esta semana olvidé "
                 "la metformina tres noches y el antihipertensivo dos mañanas."),
        ("assistant", "¿Tuviste algún síntoma nuevo o una descompensación?"),
        ("user", "Tuve más sed y dolor de cabeza leve. No tuve dolor de pecho, "
                 "falta de aire, desmayos ni visión borrosa."),
        ("assistant", "¿Qué medicación tomás y en qué dosis?"),
        ("user", "Metformina 850 mg con desayuno y cena, y losartán 50 mg por "
                 "la mañana. No tengo alergias conocidas."),
        ("assistant", "¿Tenés mediciones recientes de glucemia o presión?"),
        ("user", "Glucemia en ayunas de 145 mg/dL hace 4 días y presión de "
                 "146/92 mmHg ayer."),
    ]
    for role, content in transcript:
        db_chat.chat_save_message("interview", iv["session_key"],
                             {"role": role, "content": content})

    from app.modules.intake import analysis as intake_analysis
    real_seed_ai = str(os.environ.get("TURNOS_DEMO_SEED_REAL_AI", "1")).lower() \
        not in ("0", "false", "off", "no")
    result = intake_analysis.analyze_interview(
        iv, allow_network_when_fake=real_seed_ai)
    if result and result.get("ai_model") in ("fake", "sin_clave_api", "fallback"):
        print("  aviso: el cuestionario ficticio quedó con análisis de respaldo; "
              "se reintentará en el próximo refresh")
    db_clinical.interview_set_status(iv["id"], "done")
    db_clinical.health_profile_save(
        DEMO_PATIENT["dni"],
        {"medicacion": ["Metformina (dosis a verificar)", "Antihipertensivo (dosis a verificar)"],
         "condiciones": ["Diabetes tipo 2", "Hipertensión arterial"],
         "alergias": [], "antecedentes": ["Colecistectomía laparoscópica"]},
        source_interview_id=iv["id"],
    )

    try:
        from app.ai import chart_summary
        chart_summary.generate(DEMO_PATIENT["dni"], "gonzalez-juan", force=True)
    except Exception as exc:  # noqa: BLE001 — la demo igual queda navegable
        print(f"  aviso: resumen de Sofía se generará en el próximo tick ({exc})")
    print("  recorrido de Sofía Herrera listo (cuenta, historia, turno y consulta)")


# ------------------------------------------------------- órdenes demo ------
# The 15 synthetic sample order images (packs/example-town/ordenes-demo/, see its
# README) get cycled onto history rows with a hand-authored ai_json blob
# matching the verdict `ai_referral.analyze_referral_3pass` would actually
# produce for that image — field names/enum values confirmed against
# ai_referral.py (verdict/documento/campos shape), not paraphrased from the
# README's prose. `estados`: which booking status this order profile may
# legitimately land on — a jurisdiction-invalid/wrong-document/illegible
# order can be `pendiente` or `rechazada` (with the matching MOTIVOS_STD
# reason), never `aprobada` (that combination is nonsensical); a clean or
# merely-dudosa order can additionally be `aprobada`/`cancelada`. Every
# profile includes "pendiente" so a cycle mismatch always has a safe
# fallback (see _seed_history).
_ORDENES_DEMO_DIR = os.path.join(ROOT, "packs", "example-town", "ordenes-demo")
_MODELO_IA = "google/gemini-3-flash-preview"

_ORDENES = [
    dict(fname="synthetic-clean-digital-ecografia-1.jpg", verdict="parece_correcta",
         especialidad="Ecografía", diagnostico="Dolor abdominal inespecífico",
         practicas=["Ecografía abdominal completa"], medico="Dra. Marina Soler", matricula="00000",
         estados=("aprobada", "pendiente", "cancelada")),
    dict(fname="synthetic-clean-digital-laboratorio-1.jpg", verdict="parece_correcta",
         especialidad="Laboratorio", diagnostico="Control general",
         practicas=["Hemograma completo", "Glucemia", "Hepatograma", "Orina completa"],
         medico="Dra. Paula Ríos", matricula="00001",
         estados=("aprobada", "pendiente", "cancelada")),
    dict(fname="synthetic-clean-digital-resonancia-1.jpg", verdict="parece_correcta",
         especialidad="Diagnóstico por imágenes",
         diagnostico="Dolor persistente luego de lesión deportiva",
         practicas=["Resonancia magnética de rodilla derecha sin contraste"],
         medico="Dr. Esteban Luna", matricula="00002",
         estados=("aprobada", "pendiente", "cancelada")),
    dict(fname="synthetic-bad-handwriting-ecografia-1.jpg", verdict="dudosa",
         especialidad="Ecografía", diagnostico="Control de embarazo",
         practicas=["Ecografía obstétrica"], medico="Dra. Celina Prado", matricula="00003",
         motivo_lectura="la indicación manuscrita contiene una corrección",
         motivo="La orden no se lee con claridad.",
         estados=("aprobada", "pendiente", "rechazada")),
    dict(fname="synthetic-derivacion-neurologia-1.jpg", verdict="parece_correcta",
         especialidad="Neurología", diagnostico="Cefaleas recurrentes",
         practicas=["Interconsulta con Neurología"],
         medico="Dra. Abril Sosa", matricula="00004",
         estados=("aprobada", "pendiente", "cancelada")),
    dict(fname="synthetic-negative-wrong-document-1.jpg", verdict="documento_equivocado",
         tipo="una factura de peluquería canina", sin_datos=True,
         motivo="El documento subido no es una orden médica.",
         estados=("pendiente", "rechazada")),
    dict(fname="synthetic-two-license-cardiologia-1.jpg", verdict="parece_correcta",
         especialidad="Cardiología", diagnostico="Palpitaciones episódicas y control de soplo",
         practicas=["Interconsulta con Cardiología"],
         medico="Dr. Simón Vera", matricula="00005 / 00006",
         estados=("aprobada", "pendiente", "cancelada")),
    dict(fname="bad-handwriting-cardiologia-1.jpg", verdict="dudosa",
         especialidad="Cardiología", diagnostico="Soplo cardíaco a evaluar",
         practicas=["Interconsulta con Cardiología"], medico="Dr. Marcelo Suárez", matricula="88214",
         motivo_lectura="la letra manuscrita es difícil de confirmar con certeza",
         motivo="La orden no se lee con claridad.",
         estados=("aprobada", "pendiente", "rechazada")),
    dict(fname="bad-handwriting-laboratorio-2.jpg", verdict="dudosa",
         especialidad="Laboratorio",
         practicas=["Hemograma", "Glucemia", "Orina completa", "Colesterol"],
         medico="Dra. Patricia Núñez",
         motivo_lectura="no hay sello ni matrícula legible junto a la firma",
         motivo="Falta la firma o la matrícula del médico.",
         estados=("aprobada", "pendiente", "rechazada")),
    dict(fname="bad-handwriting-traumatologia-3.jpg", verdict="parece_correcta",
         especialidad="Traumatología", diagnostico="Esguince de tobillo",
         practicas=["Radiografía de tobillo izquierdo frente y perfil"],
         medico="Dr. Hugo Beltrán", matricula="102337",
         estados=("aprobada", "pendiente", "cancelada")),
    dict(fname="photographed-folded-laboratorio-1.jpg", verdict="parece_correcta",
         especialidad="Laboratorio", diagnostico="Control de rutina",
         practicas=["Hemograma", "Glucemia", "Orina completa"],
         medico="Dra. Fernanda Costa", matricula="33012",
         estados=("aprobada", "pendiente", "cancelada")),
    dict(fname="photographed-folded-imagenes-2.jpg", verdict="parece_correcta",
         especialidad="Diagnóstico por imágenes",
         practicas=["Ecografía renal y vesical"], medico="Dr. Julián Torres", matricula="97145",
         estados=("aprobada", "pendiente", "cancelada")),
    dict(fname="english-referral-1.jpg", verdict="parece_correcta",
         especialidad="Cardiology", diagnostico="Chest pain / shortness of breath",
         practicas=["Cardiology consultation"], medico="Dr. Susan Whitfield", matricula="48213",
         estados=("aprobada", "pendiente", "cancelada")),
    dict(fname="portuguese-sus-referral.jpg", verdict="dudosa",
         especialidad="Cardiologia", diagnostico="Dor torácica atípica",
         practicas=["Interconsulta com Cardiologia"], medico="Dr. Ricardo Almeida",
         jurisdiccion_detectada="Brasil",
         motivo="La orden fue emitida en otra jurisdicción y no corresponde a este centro.",
         estados=("pendiente", "rechazada")),
    dict(fname="foreign-jurisdiction-chile-interconsulta.jpg", verdict="dudosa",
         especialidad="Traumatología", medico="Dra. Carolina Reyes",
         practicas=["Interconsulta con Traumatología"],
         jurisdiccion_detectada="Chile",
         motivo="La orden fue emitida en otra jurisdicción y no corresponde a este centro.",
         estados=("pendiente", "rechazada")),
]
_ORDENES_BY_FNAME = {o["fname"]: o for o in _ORDENES}

# The person each sample order image actually NAMES. A seeded row that carries
# an order belongs to THIS person — the demo panel shows the image right next
# to its AI reading, and the two must agree (2026-11-10: the reading said
# «Fernanda Romero, 08/11/2026» over an image that says «Camila Fernández,
# 14/03/2026»). `dni` is the row/account key (the image's printed number where
# it has one, else a demo service number; a holder whose name is one of
# PACIENTES carries none here and takes that patient's — one person, one
# account, see the loop below); `dni_orden` is what the reading
# should claim — the printed DNI, or None for images that show none (a foreign
# document, a SUS card) so the reading honestly says it found no DNI. Names,
# births and sexes are transcribed from the images themselves.
_ORDEN_HOLDERS = {
    "synthetic-clean-digital-ecografia-1.jpg": dict(
        nombre="Lucía", apellido="Varela", dni="00000000", dni_orden="00000000",
        nacimiento=date(1990, 7, 15), sexo="F"),
    "synthetic-clean-digital-laboratorio-1.jpg": dict(
        nombre="Tomás", apellido="Benítez", dni="00000001", dni_orden="00000001",
        nacimiento=date(1990, 5, 15), sexo="M"),
    "synthetic-clean-digital-resonancia-1.jpg": dict(
        nombre="Amira", apellido="Costa", dni="00000002", dni_orden="00000002",
        nacimiento=date(1990, 9, 12), sexo="F"),
    "synthetic-bad-handwriting-ecografia-1.jpg": dict(
        nombre="Nadia", apellido="Ferrer", dni="00000003", dni_orden="00000003",
        nacimiento=date(1987, 11, 2), sexo="F"),
    "synthetic-derivacion-neurologia-1.jpg": dict(
        nombre="Ivo", apellido="Mansilla", dni="00000004", dni_orden="00000004",
        nacimiento=date(1996, 8, 12), sexo="M"),
    "synthetic-two-license-cardiologia-1.jpg": dict(
        nombre="Rocío", apellido="Lagos", dni="00000005", dni_orden="00000005",
        nacimiento=date(1992, 4, 15), sexo="F"),
    # these two images name the seed's own first two patients: no `dni` here,
    # the loop below makes each ONE person with that patient
    "bad-handwriting-cardiologia-1.jpg": dict(
        nombre="Camila", apellido="Fernández", dni_orden=None,
        nacimiento=date(1990, 6, 5), sexo="F"),
    "bad-handwriting-laboratorio-2.jpg": dict(
        nombre="Rodrigo", apellido="Ibáñez", dni_orden=None,
        nacimiento=date(1985, 2, 11), sexo="M"),
    "bad-handwriting-traumatologia-3.jpg": dict(
        nombre="Amara", apellido="Okafor", dni="99100003", dni_orden=None,
        nacimiento=date(1993, 9, 17), sexo="F"),
    "photographed-folded-laboratorio-1.jpg": dict(
        nombre="Diego", apellido="Silva", dni="32112456", dni_orden="32112456",
        nacimiento=date(1992, 5, 20), sexo="M"),
    "photographed-folded-imagenes-2.jpg": dict(
        nombre="Grace", apellido="Costa", dni="38123456", dni_orden="38123456",
        nacimiento=date(1988, 4, 25), sexo="F"),
    "english-referral-1.jpg": dict(
        nombre="John", apellido="Miller", dni="99100004", dni_orden=None,
        nacimiento=date(1985, 3, 14), sexo="M"),
    "portuguese-sus-referral.jpg": dict(
        nombre="Thiago", apellido="Ferreira", dni="99100005", dni_orden=None,
        nacimiento=date(1990, 9, 12), sexo="M"),
    "foreign-jurisdiction-chile-interconsulta.jpg": dict(
        nombre="Paciente", apellido="Ejemplo", dni="99100006", dni_orden=None,
        nacimiento=date(1980, 1, 1), sexo="F"),
}
_PACIENTE_POR_NOMBRE = {(_p["nombre"], _p["apellido"]): _p for _p in PACIENTES}
for _j, _tit in enumerate(_ORDEN_HOLDERS.values()):
    # An image that names one of the seed's own patients IS that patient — one
    # person, one account («Pacientes» listed two Camila Fernández with one
    # email otherwise): the holder takes the patient's account keys, the
    # patient takes the birth date and sex the image prints.
    _mismo = _PACIENTE_POR_NOMBRE.get((_tit["nombre"], _tit["apellido"]))
    if _mismo:
        _mismo.update(nacimiento=_tit["nacimiento"], sexo=_tit["sexo"])
        _tit.update(dni=_mismo["dni"], contacto=_mismo["contacto"], telefono=_mismo["telefono"])
    _tit.setdefault("contacto", f"{_slug(_tit['nombre'])}.{_slug(_tit['apellido'])}{MARCA}")
    _tit.setdefault("telefono", _telefono(9100 + _j))
assert len({_tit["dni"] for _tit in _ORDEN_HOLDERS.values()}) == len(_ORDEN_HOLDERS)
# the holders that are NOT one of PACIENTES: the accounts the seed adds for them
_ORDEN_HOLDERS_NUEVOS = [_tit for _tit in _ORDEN_HOLDERS.values()
                         if (_tit["nombre"], _tit["apellido"]) not in _PACIENTE_POR_NOMBRE]


def _titular_de_orden(perfil):
    """The person a seeded order row belongs to: the image's own holder when
    the profile names one, else the caller's patient (wrong-document rows have
    no holder — the image isn't an order about anybody)."""
    return _ORDEN_HOLDERS.get(perfil["fname"]) if perfil else None


def _campo(valor, coincide=None, acuerdo=None):
    if acuerdo is None:
        acuerdo = 1.0 if valor not in (None, []) else 0.0
    return {"valor": valor, "coincide": coincide, "acuerdo": acuerdo}


def _fecha_orden(i):
    """Fecha de emisión plausible y determinística (entre 2 y 23 días atrás).
    Una orden real SIEMPRE trae fecha: sin ella
    `ai_referral._verdict_3pass` la manda a «dudosa», y hace bien. Sólo los
    perfiles que declaran `falta=("fecha",)` — porque la imagen de verdad no
    la tiene — quedan sin fecha."""
    return (_now() - timedelta(days=2 + (i * 3) % 22)).strftime("%d/%m/%Y")


def _cobertura_orden(i):
    """3 de cada 4 órdenes traen obra social + número de afiliado; la cuarta
    es población exclusivamente pública. Sale de `_OBRAS_SOCIALES`, la misma
    lista que usan las cuentas — no una segunda lista paralela."""
    if i % 4 == 3:
        return None, None
    nombre, prefijo = _OBRAS_SOCIALES[i % len(_OBRAS_SOCIALES)]
    return nombre, f"{prefijo}-{4200000 + i * 3571}-01"


def _campos_orden(perfil, pac, i):
    """campos.* shape from ai_referral.py's real _verdict_3pass. The reading
    names the person ON THE IMAGE when the profile has a holder
    (`_ORDEN_HOLDERS`) — the panel shows the image beside its reading and the
    two must agree. dni/paciente match the seeded row (identity-mismatch isn't
    part of this image set's test matrix, only handwriting/jurisdiction/
    wrong-document are); `dni` is None for images that print no DNI of their
    own. Everything else comes from the profile."""
    if perfil.get("sin_datos"):
        return {k: _campo(None if k != "practicas" else [])
                for k in ("dni", "especialidad", "diagnostico", "practicas",
                          "paciente", "medico", "matricula", "fecha",
                          "prepaga", "afiliado")}
    titular = _ORDEN_HOLDERS.get(perfil["fname"])
    if titular is not None:
        pac = titular  # the row and the reading both belong to the image's person
    dni_orden = titular.get("dni_orden") if titular is not None else pac["dni"]
    especialidad = perfil.get("especialidad")
    falta = set(perfil.get("falta") or ())
    prepaga, afiliado = _cobertura_orden(i)
    fecha = None if "fecha" in falta else _fecha_orden(i)
    return {
        "dni": _campo(dni_orden, coincide=(True if dni_orden else None)),
        "especialidad": _campo(especialidad, coincide=(True if especialidad else None)),
        "diagnostico": _campo(perfil.get("diagnostico")),
        "practicas": _campo(perfil.get("practicas") or []),
        "paciente": _campo(f"{pac['nombre']} {pac['apellido']}", coincide=True),
        "medico": _campo(perfil.get("medico")),
        "matricula": _campo(perfil.get("matricula")),
        "fecha": _campo(fecha),
        "prepaga": _campo(prepaga),
        "afiliado": _campo(afiliado),
    }


def _ai_json_orden(perfil, pac, i):
    """Builds a full ai_json blob shaped exactly like
    ai_referral.analyze_referral_3pass's return value (verdict/resumen/
    campos/documento/pases/confianza/modelo/costo_usd/preparacion), phrased
    the same way the real reconciliation code phrases each verdict."""
    verdict = perfil["verdict"]
    especialidad = perfil.get("especialidad")
    medico = perfil.get("medico")
    matricula = perfil.get("matricula")
    campos = _campos_orden(perfil, pac, i)
    # Mismo cálculo que ai_referral._verdict_3pass: lo que le falta a la orden
    # se deriva de los campos, nunca se escribe a mano perfil por perfil.
    faltantes = []
    if not campos["medico"]["valor"] and not campos["matricula"]["valor"]:
        faltantes.append("firma_matricula")
    if not campos["fecha"]["valor"]:
        faltantes.append("fecha")
    documento = {"es_orden": True, "tipo": None,
                 "jurisdiccion_valida": True, "jurisdiccion_detectada": None,
                 "campos_faltantes": faltantes}
    if verdict == "documento_equivocado":
        tipo = perfil["tipo"]
        resumen = f"El documento no parece una orden médica: parece {tipo}."
        documento = {"es_orden": False, "tipo": tipo,
                     "jurisdiccion_valida": True, "jurisdiccion_detectada": None,
                     "campos_faltantes": []}
        confianza = 0.85
    elif verdict == "ilegible":
        resumen = "La orden no se pudo leer con claridad — revisala a mano."
        documento = dict(documento, campos_faltantes=[])
        confianza = 0.2
    elif perfil.get("jurisdiccion_detectada"):
        detectada = perfil["jurisdiccion_detectada"]
        resumen = (f"La orden parece emitida en otra jurisdicción ({detectada}), "
                   "no en la de este centro.")
        documento = {"es_orden": True, "tipo": None,
                     "jurisdiccion_valida": False, "jurisdiccion_detectada": detectada,
                     "campos_faltantes": faltantes}
        confianza = 0.85
    elif faltantes:
        # Identidad y especialidad cierran, pero a la orden le falta lo que la
        # hace una orden. Redactado igual que el código real.
        motivos = []
        if "firma_matricula" in faltantes:
            motivos.append("no se lee la firma ni la matrícula del médico")
        if "fecha" in faltantes:
            motivos.append("la orden no tiene fecha de emisión")
        resumen = "Revisar: " + "; ".join(motivos) + "."
        confianza = 0.6
    elif verdict == "parece_correcta":
        # Mismo giro que ai_referral.py: el DNI cuando la imagen lo trae,
        # si no el nombre del paciente.
        partes = ["DNI coincide" if campos["dni"]["valor"]
                  else "el nombre del paciente coincide"]
        if especialidad:
            partes.append(f"especialidad {especialidad}")
        if medico:
            firma = medico + (f" {matricula}" if matricula else "")
            partes.append(f"firma {firma}")
        resumen = "Los campos analizables parecen correctos: " + ", ".join(partes) + "."
        confianza = 0.9
    else:  # "dudosa" genérica (letra difícil / sin sello) — no bloquea, la revisa un humano
        motivo_lectura = perfil.get("motivo_lectura", "no se pudieron confirmar todos los datos de la orden")
        resumen = f"Revisar: {motivo_lectura}."
        confianza = 0.5
    costo = round(0.0004 + (i % 5) * 0.00015, 6)
    return {
        "verdict": verdict, "resumen": resumen, "campos": campos,
        "documento": documento, "pases": 1, "confianza": confianza,
        "modelo": _MODELO_IA, "costo_usd": costo,
        "preparacion": [], "preparacion_no_reconocidos": [],
    }


def _adjuntar_orden(cur, sid, perfil, pac, i):
    """Points the row at the sample image WHERE IT ALREADY LIVES — inside the
    pack, baked into the container image — and attaches
    referral_path/referral_mime/ai_json with a raw UPDATE on the SAME
    cursor/transaction as the INSERT (db_booking.set_referral/save_ai open their OWN
    connection each, which wouldn't see this row until the caller's
    conn.commit() — that would silently no-op).

    It used to COPY the file into booking_requests.REFERRALS_DIR to mimic
    where a real patient upload lands. That looked right and was wrong:
    `data/` is NOT a persistent volume in prod, so every redeploy wiped
    `data/ordenes/` while the rows survived in Postgres, and `_admin_row`
    correctly reported `tiene_orden=false` for all of them — the demo showed
    152 requests whose referral had evaporated (the product owner, 2026-08-05: "there are
    no images of the orders"). The daily re-seed didn't rescue them either:
    it only fires when the calendar day turns, and `seed_assets()` (which
    does run on every boot) only restored the doctor portraits.

    Pointing at the pack removes the failure mode instead of adding
    machinery to recover from it: the file can't go missing, the row is
    self-describing (the filename IS which sample it is), and nothing has to
    be copied on boot. The pack is only ever read — `stream_referral` opens
    the path, and nothing in the app deletes a referral file."""
    src = os.path.join(_ORDENES_DEMO_DIR, perfil["fname"])
    if not os.path.isfile(src):
        return False
    ai_json = _ai_json_orden(perfil, pac, i)
    br = sm.booking_requests
    sqlcore.run(cur, sa.update(br).where(br.c.id == sid).values(
        referral_path=src, referral_mime="image/jpeg", ai_json=ai_json))
    return True


def referrals_rotas():
    """True when the demo's own rows claim a referral that isn't on disk.

    The state left behind by the copy-into-`data/ordenes` era (see
    `_adjuntar_orden`), and by any future redeploy that wipes a path outside
    the pack. Scoped to the demo's marker, so a real installation's genuine
    uploads are never counted — and never repaired by deleting anything."""
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        br = sm.booking_requests
        sqlcore.run(cur, sa.select(br.c.referral_path).where(
            br.c.contact.like("%" + MARCA), br.c.referral_path.is_not(None)))
        return any(not os.path.exists(r["referral_path"]) for r in cur.fetchall())


def _insert_request(cur, pac, servicio_id, centro_slug, created, estado,
                     turno=None, medico=None, medico_id=None,
                     asistencia=None, asistencia_en=None,
                     motivo_rechazo=None, resuelto_en=None,
                     cancelado_en=None, preferencia=None):
    sqlcore.run(cur, sa.insert(sm.booking_requests).values(
        dni=pac["dni"], first_name=pac["nombre"], last_name=pac["apellido"],
        birth_date=pac["nacimiento"], sex=pac["sexo"], contact=pac["contacto"],
        service_id=servicio_id, center_slug=centro_slug, status=estado,
        appointment_at=turno, assigned_doctor=medico, assigned_doctor_id=medico_id,
        attendance=asistencia, attendance_updated_at=asistencia_en,
        attendance_updated_by=("demo-seed" if asistencia else None),
        rejection_reason=motivo_rechazo, resolved_by=("demo-seed" if resuelto_en else None),
        resolved_at=resuelto_en, cancelled_at=cancelado_en, time_preference=preferencia,
        requester_dni=pac["dni"], created=created,
    ).returning(sm.booking_requests.c.id))
    return cur.fetchone()["id"]


# One in every _LIBRE_CADA slots of a doctor's day is left empty. The day has
# to READ as a working day — nearly full, with the odd gap the front desk can
# still give away (that gap is a feature of the day view: `_free_of_day`).
_LIBRE_CADA = 7

# Services with no doctor in Sampleton (lab-like): they make up the
# «Sin médico asignado» group of the day view, on their own fixed hours.
# Laboratorio/Radiografía at the diagnostics annex are the realistic
# doctorless case (lab/imaging techs, not physicians) — every OTHER
# specialist service now has a named doctor via `_SERVICIOS` above.
# Both are «Lunes a viernes» in the pack's horario and have no agenda of
# their own, so the days are stated here (0 = Sunday, as in the pack): on a
# Saturday or Sunday the day view shows no lab and no X-ray (2026-11-08).
_LUNES_A_VIERNES = (1, 2, 3, 4, 5)
_SIN_MEDICO_HOY = (
    ("laboratorio-anexo", "diagnostic-center", (7, 0), (9, 0), _LUNES_A_VIERNES),
    ("radiografia-anexo", "diagnostic-center", (8, 0), (10, 0), _LUNES_A_VIERNES),
)



def _sin_medico_del_dia(dia):
    """The doctor-less groups of `dia`, shaped like `_agenda_de_hoy`'s."""
    semana = dia.isoweekday() % 7
    return [(None, None, svc_id, centro, _horas_fijas(desde, hasta))
            for svc_id, centro, desde, hasta, dias in _SIN_MEDICO_HOY
            if semana in dias]


# Sample order image per service of the day — only the ones whose profile
# ALLOWS an approved appointment (`estados`), so the image, the AI reading and
# the row's status stay consistent with each other.
_ORDEN_HOY_POR_SERVICIO = {
    "ecografia": "synthetic-clean-digital-ecografia-1.jpg",
    "cardiologia": "bad-handwriting-cardiologia-1.jpg",
}


def libres_de(horas):
    """Which slots of that agenda stay empty (indices).

    One in every _LIBRE_CADA, and a short agenda that would otherwise come
    out 100% full gives up its last one: a day with no free slot at all has
    nothing to show in the day view's «ver horarios libres», which is half
    of what the front desk uses that screen for."""
    libres = {i for i in range(len(horas)) if i % _LIBRE_CADA == _LIBRE_CADA - 1}
    if not libres and len(horas) >= 3:
        libres = {len(horas) - 1}
    return libres


def _servicio_de_medico(medico_id):
    for svc_id, centro, nombre, mid in _SERVICIOS:
        if mid == medico_id:
            return svc_id, centro, nombre
    return None


class AgendaDemo:
    """Every slot of a demo doctor's agenda, day by day, and the ones a
    seeded appointment already took.

    The windows are the doctor's real ones, read through the very code the
    day view and the slot generator use (`doctor_appointments.
    _windows_and_exceptions`: the services that manage the doctor's schedule,
    else its centers' windows, else the legacy flat agenda; then
    `_minutes_of_day` with `cerrado`/`extra` resolved), so a seeded
    appointment always sits on a slot that exists. The printed agenda
    (/admin/cola/print, 08.11) showed every demo appointment under «Turnos
    fuera de la agenda»: the featured patient's came at «now + 18 h» and the
    history at arbitrary hours. No appointment of a demo doctor is seeded
    outside its agenda, and no two share a slot.

    `servicio` narrows the windows to the ones of that service: a doctor of
    a cluster runs several services, each in its own hours, and the demo
    patient's Cardiología landed at 07:00 inside the ECG's window while the
    service page said «8 a 12» (15.11). Without a window of that service the
    doctor's whole agenda is used; slots are taken per doctor either way."""

    def __init__(self):
        from app.patient_web import catalog
        self._medicos = {m.get("id"): m for m in catalog.doctors() or []}
        self._ventanas = {}
        self._ocupados = set()

    def medicos(self):
        return list(self._medicos.values())

    def _de(self, medico_id, servicio):
        from app.modules.turnos import doctor_appointments as da
        from app.modules.turnos import doctor_windows
        clave = (medico_id, servicio)
        if clave not in self._ventanas:
            m = self._medicos.get(medico_id)
            ventanas = ([], [])
            if m and servicio:
                fuentes, _managed = doctor_windows.sources(m)
                franjas, excepciones = [], []
                for f in fuentes:
                    if f["servicio"] == servicio:
                        franjas += f["franjas"]
                        excepciones += f["excepciones"]
                    elif f["servicio"] is None:
                        franjas += [b for b in f["franjas"] if b.get("servicio") == servicio]
                        excepciones += f["excepciones"]
                ventanas = (franjas, excepciones)
            if m and not ventanas[0]:
                ventanas = da._windows_and_exceptions(m)  # noqa: SLF001
            self._ventanas[clave] = ventanas
        return self._ventanas[clave]

    def horas(self, medico_id, dia, servicio=None):
        """["HH:MM", …] the doctor's agenda (for `servicio`) opens on `dia`."""
        from app.modules.turnos import doctor_appointments as da
        franjas, excepciones = self._de(medico_id, servicio)
        return da._minutes_of_day(franjas, excepciones, dia.isoformat())  # noqa: SLF001

    def libres(self, medico_id, dia, servicio=None):
        return [h for h in self.horas(medico_id, dia, servicio)
                if (medico_id, dia, h) not in self._ocupados]

    def ocupar(self, medico_id, cuando):
        self._ocupados.add((medico_id, cuando.date(), cuando.strftime("%H:%M")))

    def en_agenda(self, medico_id, cuando, desde=None, hasta=None, ocupar=True, servicio=None):
        """The free slot of the doctor's agenda nearest to `cuando`: its own
        day first, then one day later, one earlier, two later… (up to two
        weeks), only between `desde` and `hasta` (dates, inclusive) and never
        TODAY — today belongs to `_seed_today_queue` alone. None when the
        doctor has no free slot in that range (or is not in the catalog)."""
        hoy = _now().date()
        minuto = cuando.hour * 60 + cuando.minute
        for paso in range(29):
            dia = cuando.date() + timedelta(days=(paso + 1) // 2 * (1 if paso % 2 else -1))
            if dia == hoy or (desde and dia < desde) or (hasta and dia > hasta):
                continue
            horas = self.libres(medico_id, dia, servicio)
            if not horas:
                continue
            hora = min(horas, key=lambda h: (abs(int(h[:2]) * 60 + int(h[3:]) - minuto), h))
            turno = datetime(dia.year, dia.month, dia.day, int(hora[:2]), int(hora[3:]),
                             tzinfo=ART)
            if ocupar:
                self.ocupar(medico_id, turno)
            return turno
        return None

    def turno(self, medico_id, cuando, desde=None, hasta=None, ocupar=True, servicio=None):
        """`en_agenda` within `desde`…`hasta`, else the nearest slot before
        `hasta` at all; `cuando` itself (moved off today) only for a doctor
        with no agenda."""
        hallado = (self.en_agenda(medico_id, cuando, desde, hasta, ocupar, servicio)
                   or self.en_agenda(medico_id, cuando, None, hasta, ocupar, servicio))
        if hallado:
            return hallado
        return cuando - timedelta(days=1) if cuando.date() == _now().date() else cuando


def _agenda_de_hoy(agendas=None):
    """[(medico_id, nombre, servicio_id, centro, ["HH:MM", …]), …] — TODAY's
    real slots, read from the catalog's own agendas through the very code
    the day view uses (`doctor_appointments._minutes_of_day`). Seeding off
    the agenda instead of hardcoded hours is what makes the demo's day
    hold together: every appointment sits on a slot that exists, the "free
    slots" toggle shows genuine gaps, and the day refills by itself
    whenever the agendas change."""
    agendas = agendas or AgendaDemo()
    dia = _now().date()
    out = []
    for m in agendas.medicos():
        mid = m.get("id")
        svc = _servicio_de_medico(mid)
        if not svc:
            continue
        horas = agendas.horas(mid, dia, svc[0])
        if horas:
            out.append((mid, m.get("nombre") or svc[2], svc[0], svc[1], horas))
    return out


# The featured patient's appointment with the demo doctor (the walkthrough:
# her pre-consult interview waits for him). It is a slot of his agenda: the
# FIRST free slot after the current hour, so the walkthrough (her card, her
# interview, «Iniciar consulta») is on screen at any time of day — not just
# in the morning. `_asegurar_turno_destacado` keeps it ahead of the clock
# between reseeds.
_MEDICO_DESTACADO = "gonzalez-juan"

# The demo service and doctor run EVERY day, 8 to 20, so the demo's scenarios
# (a future approved appointment, the doctor's day) exist on any date and at
# any hour the demo is shown — a weekday-morning-only catalog made the demo
# empty after noon and on weekends. Declared as a catalog OVERLAY the seed
# re-asserts idempotently (demo-only, never the pack: the pack is generated),
# so it also follows the pack across regenerations instead of freezing it.
_SERVICIO_DEMO = "medicina-general-riverside-clinic"
_AGENDA_DEMO = {"dias": [0, 1, 2, 3, 4, 5, 6], "desde": "08:00", "hasta": "20:00", "cada_min": 20}
# `horario` is a PARSED string (hours.mjs / site_schedule): it must name the
# days the way the pack does — «Todos los días…» parses to no openings at all.
_HORARIO_DEMO = "Lunes a domingo de 8 a 20 h"


def _restaurar_medicos_sin_centro():
    """Drop a doctor overlay that names a center this catalog does not have,
    so the doctor goes back to the pack's own row.

    A September edit left Carlos Wu on Pinamar's «caps-sur» in the demo's
    overlay; the daily refresh never touched it and the panel showed
    «Centro sin nombre». Only doctors the pack itself holds are restored —
    a doctor created in the panel is never deleted here. Returns True when
    a row was dropped."""
    from app.patient_web import catalog
    centros = {c.get(catalog.id_key("centros")) for c in catalog.base_items("centros")}
    filas_centros = db_catalog.catalog_list("centros")
    centros |= {f.get("id") for f in filas_centros}
    centros -= {f.get("id") for f in filas_centros if f.get("borrado")}
    base = {m.get("id") for m in catalog.base_items("medicos")}
    cambios = False
    for fila in db_catalog.catalog_list("medicos"):
        if fila.get("borrado"):
            continue  # deleted in the panel: dropping the row would bring the doctor back
        doc = fila.get("doc") or {}
        nombrados = {doc.get("centro")} | {c.get("centro") for c in doc.get("centros") or []}
        nombrados.discard(None)
        nombrados.discard("")
        if fila.get("id") in base and nombrados - centros:
            db_catalog.catalog_delete_row("medicos", fila.get("id"))
            cambios = True
    return cambios


def _asegurar_catalogo_demo():
    """Idempotently re-assert the demo's own catalog overlay: the demo service
    open every day 8-20 and symptom capture on for the demo doctor.

    The overlay doc is rebuilt from the PACK's current base item on every
    call, so a pack change flows through (a stale overlay would otherwise
    freeze the row at whatever the pack said when it was first written).
    Returns True when something changed."""
    from app.patient_web import catalog
    cambios = False
    for tipo, id_, editar in (
        ("servicios", _SERVICIO_DEMO, lambda d: (d.update(agenda=dict(_AGENDA_DEMO), horario=_HORARIO_DEMO), d)[1]),
        ("medicos", _MEDICO_DESTACADO, lambda d: (d.update(entrevista_habilitada=True), d)[1]),
    ):
        base = next((it for it in catalog.base_items(tipo) if it.get(catalog.id_key(tipo)) == id_), None)
        if base is None:
            continue  # a pack without the demo's showcase row: nothing to overlay
        doc = editar(dict(base))
        fila = db_catalog.catalog_get(tipo, id_)
        if fila is None or fila.get("borrado") or (fila.get("doc") or {}) != doc:
            db_catalog.catalog_upsert(tipo, id_, doc)
            cambios = True
    cambios = _restaurar_medicos_sin_centro() or cambios
    if cambios:
        catalog.invalidate()
    return cambios


def _slots_tomados(cur, medico_id, sid):
    """Every appointment time of `medico_id` another live booking holds (DB
    truth — the in-memory `AgendaDemo` only lives inside a seed run)."""
    br = sm.booking_requests
    return {r["appointment_at"] for r in sqlcore.run(cur, sa.select(br.c.appointment_at).where(
        br.c.assigned_doctor_id == medico_id,
        br.c.status.in_(("aprobada", "pendiente")),
        br.c.id != sid,
        br.c.appointment_at.is_not(None))).fetchall()}


def _slot_libre_destacado(cur, sid, desde_dt):
    """The demo doctor's first agenda slot of `desde_dt`'s day, from that
    moment on, that no other booking holds; None once that day's agenda is
    over. Never a slot outside the agenda: on the demo a row outside the
    doctor's hours reads as a bug (decision 2026-11-08)."""
    ocupados = _slots_tomados(cur, _MEDICO_DESTACADO, sid)
    abre = tuple(int(x) for x in _AGENDA_DEMO["desde"].split(":"))
    cierra = tuple(int(x) for x in _AGENDA_DEMO["hasta"].split(":"))
    dia = desde_dt.date()
    for hora in _horas_fijas(abre, cierra, cada_min=_AGENDA_DEMO["cada_min"]):
        cuando = datetime(dia.year, dia.month, dia.day, int(hora[:2]), int(hora[3:]), tzinfo=ART)
        if cuando >= desde_dt and cuando not in ocupados:
            return cuando
    return None


def _asegurar_turno_destacado():
    """Keep the walkthrough alive between reseeds (demo_refresh runs this
    every tick): the featured patient's TODAY appointment with the demo
    doctor stays approved and, while his agenda has a slot left, in the
    future.

    A visitor of the patient demo can cancel her appointment (the only demo
    session that writes); time itself passes the slot as the day goes on.
    Both are repaired here with a plain marker-scoped UPDATE, the same write
    style the seed itself uses (these are the seed's own fictional rows being
    re-asserted, not a panel action; the seed never writes
    appointment_events):

    - with the demo doctor, a LAPSED slot moves to the next free slot of the
      same day, and a cancelled appointment still ahead of the clock comes
      back on its own slot (the next free one only when somebody took it
      meanwhile). After the day's last slot nothing moves: the appointment
      stays on today's list, approved, where the doctor's «Hoy» still shows
      her card — never on tomorrow's, never outside the agenda — and the
      midnight reseed deals the new day;
    - her appointment with another service is only ever restored in place,
      and only while its slot is still free — otherwise it stays cancelled
      until the reseed.

    Each row is its own transaction: one that cannot be written (a slot taken
    between the read and the write) never undoes the others. Nothing found
    simply returns: a reseed rebuilds everything anyway."""
    ahora = _now()
    br = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        filas = sqlcore.run(cur, sa.select(br).where(
            br.c.dni == DEMO_PATIENT["dni"], br.c.contact.like("%" + MARCA),
            br.c.appointment_at.is_not(None),
            br.c.appointment_at >= ahora.replace(hour=0, minute=0, second=0, microsecond=0),
        ).order_by(br.c.appointment_at)).fetchall()
        cambio = False
        for fila in filas:
            sid, medico_id, actual = fila["id"], fila["assigned_doctor_id"], fila["appointment_at"]
            vencido = actual < ahora
            vivo = fila["status"] == "aprobada" and not fila["cancelled_at"]
            if vivo and not vencido:
                continue
            if medico_id == _MEDICO_DESTACADO:
                desde = ahora + timedelta(minutes=10) if vencido else actual
                destino = _slot_libre_destacado(cur, sid, desde) or actual
            elif vencido:
                continue
            else:
                destino = actual
            if destino == actual and (vivo or actual in _slots_tomados(cur, medico_id, sid)):
                continue  # the day's agenda is over, or somebody holds her slot
            try:
                sqlcore.run(cur, sa.update(br).where(br.c.id == sid).values(
                    status="aprobada", cancelled_at=None, appointment_at=destino))
                conn.commit()
                cambio = True
            except Exception:  # noqa: BLE001 — this row waits for the next tick
                conn.rollback()
        return cambio


def _turno_destacado(agendas):
    ahora = _now()
    hoy = ahora.date()
    libres_hoy = agendas.libres(_MEDICO_DESTACADO, hoy)
    futuras = [h for h in libres_hoy if h > ahora.strftime("%H:%M")]
    # Later today; once his day's agenda is over, the day's LAST free slot —
    # she stays on today's list (the doctor lands on «Hoy»), never tomorrow's.
    dia, horas = hoy, futuras or libres_hoy[-1:]
    for n in range(1, 15):
        if horas:
            break
        dia = hoy + timedelta(days=n)
        horas = agendas.libres(_MEDICO_DESTACADO, dia)
    if not horas:
        return ahora + timedelta(hours=18)  # a catalog without the demo doctor
    hora = horas[0]
    turno = datetime(dia.year, dia.month, dia.day, int(hora[:2]), int(hora[3:]), tzinfo=ART)
    agendas.ocupar(_MEDICO_DESTACADO, turno)
    return turno


def _horas_fijas(desde, hasta, cada_min=30):
    h, m = desde
    fin = hasta[0] * 60 + hasta[1]
    out = []
    while h * 60 + m < fin:
        out.append(f"{h:02d}:{m:02d}")
        m += cada_min
        h, m = h + m // 60, m % 60
    return out


def _seed_today_queue(agendas=None):
    """TODAY's approved appointments — «Pacientes de hoy» (#cola) needs a
    live, believable day, not 90-day history.

    Every doctor's agenda for today is filled almost completely (one slot in
    _LIBRE_CADA stays free), plus the two doctor-less services. What already
    happened by the time this runs is marked: mostly «presente», one in six
    «ausente», and the last two are deliberately left unmarked so the day
    view shows its "N turnos pasaron sin registrar asistencia" warning —
    that nudge is half of what that screen is for.

    Because this reads TODAY's agenda, re-running it tomorrow produces
    tomorrow's day: that is how the demo stays current (demo_refresh.py runs
    it every day just after midnight). Also (re)creates the médico login so
    the doctor-only view has something to show."""
    medianoche = _now().replace(hour=0, minute=0, second=0, microsecond=0)
    ayer = _now() - timedelta(days=1)
    ahora = _now()

    agendas = agendas or AgendaDemo()
    grupos = _agenda_de_hoy(agendas)
    grupos += _sin_medico_del_dia(medianoche.date())

    filas, reservadas = [], 0
    for gi, (medico_id, medico, svc_id, centro, horas) in enumerate(grupos):
        libres = libres_de(horas)
        # A slot already taken (the featured patient's) is neither filled again nor free.
        tomadas = (set(horas) - set(agendas.libres(medico_id, medianoche.date(), svc_id))
                   if medico_id else set())
        for hi, hora in enumerate(horas):
            if hora in tomadas:
                reservadas += 1
                continue
            if hi in libres:
                continue  # the slot the front desk can still give away
            hh, mm = (int(x) for x in hora.split(":"))
            cuando = medianoche.replace(hour=hh, minute=mm)
            if medico_id:
                agendas.ocupar(medico_id, cuando)
            filas.append((medico_id, medico, svc_id, centro, cuando, gi, hi))

    # Attendance is decided over the day as a whole (not per doctor), so the
    # "not yet registered" tail is the last couple of slots of the DAY.
    filas.sort(key=lambda f: (f[4], f[5]))
    pasados = [i for i, f in enumerate(filas) if f[4] <= ahora]
    sin_registrar = set(pasados[-2:])

    con_orden, con_orden_svc = 0, set()
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        for idx, (medico_id, medico, svc_id, centro, cuando, _gi, _hi) in enumerate(filas):
            # A couple of rows carry a synthetic referral image, and only where
            # one belongs: a sample order whose specialty matches the
            # service, never a cardiology order stapled to a pediatrics
            # appointment. Most rows have nothing to read, as in real life.
            # The row that carries one belongs to the person ON the image
            # (`_ORDEN_HOLDERS`): the panel shows the image beside its reading.
            fname = next((f for pref, f in _ORDEN_HOY_POR_SERVICIO.items()
                          if svc_id.startswith(pref)), None)
            perfil = (_ORDENES_BY_FNAME[fname]
                      if fname and con_orden < 3 and svc_id not in con_orden_svc else None)
            pac = _titular_de_orden(perfil) or PACIENTES[(idx * 7 + cuando.day) % len(PACIENTES)]
            asis = None
            if cuando <= ahora and idx not in sin_registrar:
                asis = "ausente" if idx % 6 == 5 else "presente"
            sid = _insert_request(cur, pac, svc_id, centro, ayer,
                                  "aprobada", turno=cuando, medico=medico,
                                  medico_id=medico_id, asistencia=asis,
                                  asistencia_en=(cuando if asis else None),
                                  # Resolved within hours of asking, like the
                                  # history rows (3 h): same-instant approvals
                                  # dragged the report's «hours to resolve»
                                  # median to 0.0 h on the demo.
                                  resuelto_en=ayer + timedelta(hours=1 + idx % 5))
            if perfil:
                # one try per service, attached or not: a sample image missing
                # from the pack must not hand the service's whole day to its
                # holder
                con_orden_svc.add(svc_id)
                if _adjuntar_orden(cur, sid, perfil, pac, idx):
                    con_orden += 1
        conn.commit()
    libres = sum(len(g[4]) for g in grupos) - len(filas) - reservadas
    print(f"  {len(filas)} turnos de hoy creados en {len(grupos)} agendas "
          f"({libres} horarios libres, {con_orden} con orden adjunta)")

    usuario = "gonzalez"
    if db_operators.operator_get(usuario):
        db_operators.operator_update(usuario, rol="medico", activo=True,
                          password=PUBLIC_DOCTOR_LOGIN, medico_id="gonzalez-juan")
    else:
        db_operators.operator_create(usuario, PUBLIC_DOCTOR_LOGIN, rol="medico",
                          medico_id="gonzalez-juan")
    # The demo doctor is a doctor already at work — profile, agendas and a full
    # day — so its account is «Perfil completo», never «Invitado», and its
    # login opens the day's work instead of the first-day onboarding
    # (audit 2026-11-05). Idempotent: the first completion time stays.
    db_operators.operator_complete_onboarding(usuario)
    print("  operador 'gonzalez' listo (rol medico, clave DemoGonzalez2026)")


def _historia_idx(i):
    """Which fictional patient booking #i (of _seed_history) belongs to.
    1 of every 3 rows revisits someone WITH a registered account (index <
    _N_CUENTAS) instead of marching flatly through all 40 — so a chunk of
    patients build up real history (repeat visits, a rejection, a no-show)
    on their own profile instead of every patient having exactly one row."""
    if i % 3 == 2:
        return i % _N_CUENTAS
    return (i + 10) % len(PACIENTES)


# An order belongs to the request it is attached to (TASK-58, 26.10): the
# panel showed «Especialidad: Neurología ✓ coincide» on a gynecology request
# because the history dealt the sample orders out regardless of service.
# A profile that names a specialty goes only to that specialty's service
# (its slug opens the service id); the wrong-document one, which names
# none, now and then lands anywhere — a desk does receive those.
_ESPECIALIDAD_ALIAS = {"cardiology": "cardiologia"}


def _perfil_para(svc_id, i):
    propios = []
    for p in _ORDENES:
        esp = p.get("especialidad")
        if not esp:
            continue
        clave = _ESPECIALIDAD_ALIAS.get(_slug(esp), _slug(esp))
        if svc_id.startswith(clave):
            propios.append(p)
    if propios:
        return propios[i % len(propios)]
    if i % 4 == 0:
        return next(p for p in _ORDENES if not p.get("especialidad"))
    return None


def _seed_history(agendas=None):
    """~180 booking requests over the last 90 days, realistic state mix, so
    Reportes (recupero/estadística/capacidad) has a real shape to show and a
    subset of patients read as returning patients, not one-off rows.

    5 of every 6 rows (i % 6 != 5) also get one of the 15 sample order
    images cycled on deterministically — the remaining 1-in-6 legitimately
    has no order (real-world variance, not every request needs a referral).
    The row's `estado` comes from the same `ciclo_estados` cycle as before,
    EXCEPT when that estado isn't in the attached order profile's allowed
    set (`_ORDENES[...]["estados"]`, see its definition above) — e.g. the
    cycle can't land "aprobada" on a wrong-document/illegible/foreign-
    jurisdiction order, so it falls back to "pendiente" (always allowed)
    instead, keeping every row's estado/ai_json combination internally
    consistent.

    Every appointment date sits on a free slot of its doctor's agenda
    (`AgendaDemo.en_agenda`), never today and never before the request:
    the doctor's calendar and the printed agenda of a past day read as a
    working day, not as a scatter of night-time visits."""
    agendas = agendas or AgendaDemo()
    ayer = _now().date() - timedelta(days=1)
    n = 180
    ciclo_estados = (["aprobada"] * 4 + ["pendiente"] * 2
                     + ["rechazada"] * 2 + ["cancelada"])
    creadas = {"aprobada": 0, "pendiente": 0, "rechazada": 0, "cancelada": 0}
    waitlist_rows = []
    ordenes_adjuntas = 0
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        for i in range(n):
            dias_atras = 2 + (i * 87 // n)
            svc_id, centro, doc, doc_id = _SERVICIOS[i % len(_SERVICIOS)]

            perfil = _perfil_para(svc_id, i) if i % 6 != 5 else None
            # An order row belongs to the person the image itself names
            # (`_ORDEN_HOLDERS`), so the history row and the AI reading of
            # its order agree on WHO the order is about.
            pac = _titular_de_orden(perfil) or PACIENTES[_historia_idx(i)]
            tiene_orden = perfil is not None
            estado = ciclo_estados[i % len(ciclo_estados)]
            if tiene_orden and estado not in perfil["estados"]:
                # Deterministic pick within the profile's own allowed set —
                # NOT a hardcoded "pendiente" fallback: for profiles whose
                # estados is exactly (pendiente, rechazada) (illegible,
                # wrong-document, foreign-jurisdiction), always landing on
                # "pendiente" here left every jurisdiction-rejected order
                # sitting as pendiente and never as an actual rechazada row
                # with its real MOTIVOS_STD reason — found by querying a
                # live reset+seed, not by inspection alone.
                estado = perfil["estados"][i % len(perfil["estados"])]
            creadas[estado] += 1
            created = _now() - timedelta(days=dias_atras, hours=i % 24)

            kwargs = dict(medico=doc, medico_id=doc_id)
            pasado = False
            if estado == "aprobada":
                turno = agendas.turno(doc_id, created + timedelta(days=2, hours=i % 6),
                                      desde=(created + timedelta(days=1)).date(), hasta=ayer,
                                      servicio=svc_id)
                # the newest requests: the slot found may come before the
                # request would have; the request is then made two days earlier
                created = min(created, turno - timedelta(days=2))
                pasado = turno < _now()
                kwargs.update(
                    turno=turno,
                    resuelto_en=created + timedelta(hours=3),
                    asistencia=("presente" if pasado and i % 3 else
                                ("ausente" if pasado and i % 3 == 0 else None)),
                    asistencia_en=(turno + timedelta(minutes=10) if pasado else None),
                )
            elif estado == "rechazada":
                motivo = (perfil["motivo"] if tiene_orden and perfil.get("motivo")
                          else _MOTIVOS_RECHAZO[i % len(_MOTIVOS_RECHAZO)])
                kwargs.update(
                    resuelto_en=created + timedelta(hours=5),
                    motivo_rechazo=motivo,
                )
            elif estado == "cancelada":
                # a cancelled appointment gave its slot back: it takes none
                turno = agendas.turno(doc_id, created + timedelta(days=3),
                                      desde=(created + timedelta(days=2)).date(), ocupar=False,
                                      servicio=svc_id)
                kwargs.update(turno=turno, cancelado_en=created + timedelta(days=1))
            # "pendiente": no extra fields — still waiting on the hospital.

            sid = _insert_request(cur, pac, svc_id, centro, created, estado, **kwargs)
            if tiene_orden and _adjuntar_orden(cur, sid, perfil, pac, i):
                ordenes_adjuntas += 1
            if estado == "aprobada" and pasado and i % 5 == 0:
                # a few of the "already happened" ones want a sooner slot next
                # time — feeds the waitlist for realism.
                waitlist_rows.append((sid, pac, svc_id, centro, turno))
        conn.commit()

        for sid, pac_w, svc_w, centro_w, turno_w in waitlist_rows:
            sqlcore.run(cur, sa.insert(sm.waitlist).values(
                request_id=sid, dni=pac_w["dni"], email=pac_w["contacto"], service_id=svc_w,
                center_slug=centro_w, appointment_at=turno_w, active=sa.true()))
        conn.commit()
    print(f"  {n} solicitudes históricas creadas ({creadas}), "
          f"{ordenes_adjuntas} con orden adjunta, "
          f"{len(waitlist_rows)} en lista de espera")


# ------------------------------------------------- the doctors' agendas ----
#
# 2026-11-15: the demo read as dead — 11 appointments on 8 549 slots of the
# next 14 days, «Ocupación 14 días» filled for 1 doctor of 68, every report
# point at zero. Today's queue and the 180-row history only ever touched the
# eleven `_SERVICIOS` doctors. This part books EVERY doctor's agenda: from
# tomorrow to 14 days out (each doctor its own share, more near, less far,
# 40-50 % over the whole demo), and the last eight weeks with attended,
# missed and cancelled visits. Never a full day: a patient of the demo always
# finds free hours.
#
# The agenda's patients carry their own contact suffix (MARCA_AGENDA, still a
# MARCA suffix: the marked delete takes them) so the day-coverage check
# (`cola_del_dia`) never takes tomorrow's agenda for tomorrow's QUEUE — the
# midnight reseed would then skip the day. A contact, not an audit column:
# staff actions rewrite `resolved_by`, never the patient's contact.
MARCA_AGENDA = "+agenda" + MARCA
# Every day a visit can be asked for is booked, so the requests made each day
# stay level up to today (14 days left the last week a sixth short).
_AGENDA_DIAS_FUTUROS = 19
_AGENDA_DIAS_PASADOS = 56
# How far ahead a visit is asked for: 1 to this many days. The future fill
# divides by what is left of it, so it must reach past the agenda's last day.
_AGENDA_ANTELACION = 20
# The share of the doctor's own base a day ends with, past or future alike.
_AGENDA_LLENO = 1.2
assert _AGENDA_DIAS_FUTUROS < _AGENDA_ANTELACION
# A day is never booked above this share, and keeps at least one free slot.
_AGENDA_TOPE = 85
# The walkthrough doctor's days stay visibly open for a visitor booking them.
_AGENDA_TOPE_DESTACADO = 50
_N_PACIENTES_AGENDA = 1200
_MODOS_CON_TURNO = ("solicitud", "solicitud_con_orden")
# Off in the seed tests that are not about the agendas: ~21 000 rows a seed.
LLENAR_AGENDAS = True


def _h(*partes):
    """Deterministic spread in 0..99 — the seed never uses `random`."""
    return zlib.crc32("|".join(str(p) for p in partes).encode()) % 100


def _pacientes_agenda():
    """1 200 more fictional people for the agendas. No accounts: most of a
    hospital's patients are booked at the desk or by phone and never sign
    up. Name pairs that the `PACIENTES` zip never makes; DNIs in a range of
    their own, clear of the seed's 30 000 000 + i·137 and of every DNI the
    tests write."""
    out = []
    hoy = _now().date()
    n = len(_NOMBRES)
    for k in range(_N_PACIENTES_AGENDA):
        a, q = k % n, k // n + 1
        nombre, apellido = _NOMBRES[a], _APELLIDOS[(a + q) % n]
        edad = 1 + (k * 37) % 88
        out.append(dict(
            dni=str(31230000 + k * 53), nombre=nombre, apellido=apellido,
            nacimiento=date(hoy.year - edad, (k % 12) + 1, (k * 5) % 27 + 1),
            sexo="F" if a % 2 == 0 else "M",
            contacto=f"{_slug(nombre)}.{_slug(apellido)}.{k}{MARCA_AGENDA}",
        ))
    return out


def _pool_de(cluster, svc_id, pool):
    """Children for pediatrics, women for gynecology; adults for the rest."""
    clave = f"{cluster} {svc_id}"
    adulto = _now().year - 15
    if "pediatr" in clave:
        return [p for p in pool if p["nacimiento"].year > adulto]
    if "gineco" in clave or "obstetr" in clave:
        return [p for p in pool if p["sexo"] == "F" and p["nacimiento"].year <= adulto]
    return [p for p in pool if p["nacimiento"].year <= adulto]


class _LugaresDelMedico:
    """Where and under which service each slot of one doctor's day is booked.

    The doctor's agenda comes one source per center or managing service
    (`doctor_windows.sources`, the same the printed day sheet and the public
    page read), so a slot belongs to the center whose window it falls in —
    never a Tuesday appointment at the center the doctor only works on
    Mondays. The service is the window's own (a managing service or a band's
    `servicio` tag) when it books, else the doctor's first booking service of
    its specialty at that center; a window with neither (a walk-in desk) is
    never booked."""

    def __init__(self, m, servicios):
        from app.modules.turnos import doctor_windows
        from app.patient_web import catalog
        self.id = m.get("id")
        self.nombre = m.get("nombre") or ""
        self._fuentes, _ = doctor_windows.sources(m, servicios)
        self._reservables = {s["id"]: s for s in servicios
                             if s.get("modo") in _MODOS_CON_TURNO and s.get("id")}
        clusters = catalog.doctor_clusters(m)
        # {center: [service ids]}: the doctor's own service first, then the
        # bookable services of its specialty at that center, by id
        self._por_centro = {}
        propio = _servicio_de_medico(self.id)
        if propio and propio[0] in self._reservables:
            self._por_centro[propio[1]] = [propio[0]]
        for s in sorted(self._reservables.values(), key=lambda s: s["id"]):
            if s.get("cluster") in clusters and s["id"] not in self._por_centro.get(s.get("centro"), []):
                self._por_centro.setdefault(s.get("centro"), []).append(s["id"])

    def servicio(self, svc_id):
        return self._reservables[svc_id]

    def _abre(self, svc_id, dia, hora):
        """Whether service `svc_id`'s own hours hold `hora` on `dia`: a
        service with no agenda of its own holds any hour of its doctors."""
        agenda = self._reservables[svc_id].get("agenda") or {}
        bandas = agenda.get("franjas") or ([agenda] if agenda.get("dias") else [])
        if not bandas:
            return True
        semana = dia.isoweekday() % 7
        return any(semana in b["dias"] and b["desde"] <= hora < b["hasta"] for b in bandas)

    def del_dia(self, dia):
        """{"HH:MM": service id} for the slots of `dia` this doctor books.

        A window with no service of its own is booked under the first of the
        doctor's services at that center whose own hours hold the slot: the
        tomography doctor's Tuesday 10:00 was booked as «Ecografía
        abdominal», a service that opens Monday, Wednesday and Friday
        (demo review 2026-11-15)."""
        from app.modules.turnos import doctor_appointments as da
        out = {}
        for f in self._fuentes:
            for banda in f["franjas"] or []:
                svc = next((x for x in (banda.get("servicio"), f["servicio"])
                            if x in self._reservables), None)
                candidatos = [svc] if svc else self._por_centro.get(f["centro"], [])
                candidatos = [x for x in candidatos
                              if self._reservables[x].get("centro") == f["centro"]]
                if not candidatos:
                    continue
                for hora in da._minutes_of_day([banda], f["excepciones"], dia):  # noqa: SLF001
                    elegido = next((x for x in candidatos if self._abre(x, dia, hora)), None)
                    if elegido:
                        out.setdefault(hora, elegido)
        return out


def _base_de(mid):
    """The doctor's own booked share, 30..65 %: some agendas busy, some not."""
    return _AGENDA_TOPE_DESTACADO - 15 if mid == _MEDICO_DESTACADO else 30 + _h("base", mid) % 36


def _fila(pac, svc, medico, estado, turno, created, solicitante=None, **extra):
    fila = dict(
        dni=pac["dni"], first_name=pac["nombre"], last_name=pac["apellido"],
        birth_date=pac["nacimiento"], sex=pac["sexo"], contact=pac["contacto"],
        service_id=svc["id"], center_slug=svc.get("centro"), status=estado, appointment_at=turno,
        assigned_doctor=medico.nombre, assigned_doctor_id=medico.id, attendance=None,
        attendance_updated_at=None, attendance_updated_by=None, rejection_reason=None,
        resolved_by=None, resolved_at=None, cancelled_at=None, time_preference=None,
        requester_dni=(solicitante or pac)["dni"], created=created)
    fila.update(extra)
    return fila


class _Pacientes:
    """The agenda's patient pool, split once per service (children, women)."""

    def __init__(self):
        self.todos = _pacientes_agenda()
        self.adultos = _pool_de("", "", self.todos)
        self._por_servicio = {}

    def de(self, svc):
        if svc["id"] not in self._por_servicio:
            self._por_servicio[svc["id"]] = (
                _pool_de(svc.get("cluster") or "", svc["id"], self.todos) or self.todos)
        return self._por_servicio[svc["id"]]

    def elegir(self, svc, *semilla):
        """(patient, requester): a child's appointment is booked by an adult."""
        pool = self.de(svc)
        x = zlib.crc32("|".join(map(str, semilla)).encode())
        pac = pool[x % len(pool)]
        adulto = self.adultos[(x // 7) % len(self.adultos)]
        return pac, (adulto if pac["nacimiento"].year > _now().year - 15 else None)


def _filas_del_dia(agendas, medico, dia, ahora, pacientes):
    """The rows of one doctor's day: the slots it books (occupied in the
    ledger) and, now and then, a cancelled visit that gave its slot back."""
    mid = medico.id
    lugares = medico.del_dia(dia)
    total = agendas.horas(mid, dia)
    libres = [h for h in agendas.libres(mid, dia) if h in lugares]
    if len(total) < 2 or len(libres) < 2:
        return []
    distancia = (dia - ahora.date()).days
    pct = _base_de(mid)
    if distancia > 0:
        # Booked so far, never booked later: a visit is asked for 1 to
        # _AGENDA_ANTELACION days ahead, so of a day `distancia` away only
        # the share asked for at least that far ahead exists yet — 130 % of
        # the doctor's share tomorrow, 46 % in two weeks. A flat future fill
        # booked in the last days put 3.2 thousand requests in the current
        # week against 2 thousand in every other one. A past day ends at the
        # SAME share (_AGENDA_LLENO): with tomorrow at 130 % against 90 % for
        # a past day, the last two weeks asked for a fifth more visits.
        pct = pct * _AGENDA_LLENO * (_AGENDA_ANTELACION + 1 - distancia) / _AGENDA_ANTELACION
    else:
        pct = pct * _AGENDA_LLENO
    pct += _h("dia", mid, dia) % 17 - 8
    tope = _AGENDA_TOPE_DESTACADO if mid == _MEDICO_DESTACADO else _AGENDA_TOPE
    pct = max(0, min(pct, tope))
    ya = len(total) - len(agendas.libres(mid, dia))
    n = min(round(len(total) * pct / 100) - ya, len(libres) - 1)
    elegidas = sorted(libres, key=lambda h: (_h("slot", mid, dia, h), h))[:max(n, 0)]
    filas = []
    for hora in sorted(elegidas):
        svc = medico.servicio(lugares[hora])
        turno = datetime(dia.year, dia.month, dia.day, int(hora[:2]), int(hora[3:]), tzinfo=ART)
        x = _h("fila", mid, dia, hora)
        pac, solicitante = pacientes.elegir(svc, "pac", mid, dia, hora)
        if distancia > 0:
            # asked for between _AGENDA_ANTELACION days before the visit and now
            created = ahora - timedelta(days=x % (_AGENDA_ANTELACION + 1 - distancia),
                                        hours=6 + x % 9)
        else:
            created = turno - timedelta(days=1 + x % _AGENDA_ANTELACION, hours=x % 9)
            if created > ahora - timedelta(hours=6):
                created = ahora - timedelta(hours=6 + x % 40)
        resuelto = created + timedelta(hours=1 + x % 5)
        agendas.ocupar(mid, turno)
        extra = dict(resolved_by="demo-seed", resolved_at=resuelto)
        if distancia < 0:
            # the past: ~86 % came, the rest did not
            extra.update(attendance="ausente" if x >= 86 else "presente",
                         attendance_updated_by="demo-seed",
                         attendance_updated_at=turno + timedelta(minutes=10))
        filas.append(_fila(pac, svc, medico, "aprobada", turno, created, solicitante, **extra))
    # About one cancelled visit for every eight booked: the patient called it
    # off and the slot went back to the agenda (it occupies nothing).
    n_cancel = (len(elegidas) + _h("ncancel", mid, dia) % 8) // 8
    resto = sorted((h for h in libres if h not in elegidas),
                   key=lambda h: (_h("cancel", mid, dia, h), h))
    for hora in sorted(resto[:n_cancel]):
        svc = medico.servicio(lugares[hora])
        turno = datetime(dia.year, dia.month, dia.day, int(hora[:2]), int(hora[3:]), tzinfo=ART)
        x = _h("filac", mid, dia, hora)
        pac, solicitante = pacientes.elegir(svc, "cancel", mid, dia, hora)
        created = min(turno - timedelta(days=2 + x % 15), ahora - timedelta(hours=8 + x % 30))
        cancelado = min(created + (turno - created) / 2, ahora - timedelta(hours=1))
        filas.append(_fila(pac, svc, medico, "cancelada", turno, created, solicitante,
                           cancelled_at=cancelado))
    return filas


def _ocupar_lo_que_hay(agendas, desde, hasta):
    """Marks in the ledger every approved slot already in the database: a
    demo visitor's own booking (not a marked row, a reseed keeps it) holds
    its slot, and the database allows one approved appointment per doctor
    and minute (`booking_requests_doctor_slot_uniq`)."""
    br = sm.booking_requests
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        filas = sqlcore.run(cur, sa.select(br.c.assigned_doctor_id, br.c.appointment_at).where(
            br.c.status == "aprobada", br.c.assigned_doctor_id.is_not(None),
            br.c.appointment_at >= desde, br.c.appointment_at < hasta)).fetchall()
    for f in filas:
        agendas.ocupar(f["assigned_doctor_id"], f["appointment_at"].astimezone(ART))


def _seed_agendas(agendas):
    """Books every doctor's agenda around what the seed and the visitors
    already hold (today's queue, the history, the walkthrough patient)."""
    from app.patient_web import catalog
    ahora = _now()
    hoy = ahora.date()
    inicio = datetime(hoy.year, hoy.month, hoy.day, tzinfo=ART)
    _ocupar_lo_que_hay(agendas, inicio - timedelta(days=_AGENDA_DIAS_PASADOS),
                       inicio + timedelta(days=_AGENDA_DIAS_FUTUROS + 1))
    servicios = catalog.services() or []
    pacientes = _Pacientes()
    filas, medicos = [], 0
    for m in sorted(agendas.medicos(), key=lambda m: str(m.get("id"))):
        if not m.get("id"):
            continue
        medico = _LugaresDelMedico(m, servicios)
        antes = len(filas)
        for d in range(-_AGENDA_DIAS_PASADOS, _AGENDA_DIAS_FUTUROS + 1):
            if d == 0:
                continue  # today belongs to `_seed_today_queue` alone
            filas += _filas_del_dia(agendas, medico, hoy + timedelta(days=d), ahora, pacientes)
        medicos += len(filas) > antes
    if filas:
        # one prepared INSERT run over every row: ~21 000 rows, compiled once
        insert = sqlcore.prepare(sa.insert(sm.booking_requests).values(
            {k: sa.bindparam("v_" + k) for k in filas[0]}))
        with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
            insert.run_many(cur, [{"v_" + k: v for k, v in f.items()} for f in filas])
            conn.commit()
    futuras = sum(1 for f in filas if f["status"] == "aprobada" and f["appointment_at"] > ahora)
    print(f"  agendas: {len(filas)} turnos en {medicos} médicos "
          f"({futuras} por venir en {_AGENDA_DIAS_FUTUROS} días)")


# Most doctors of a working installation have an account, at every stage of
# it; some still have none. Deterministic per doctor; the walkthrough doctor
# keeps its own («gonzalez», `_seed_today_queue`). Accounts are not marked
# rows: a reseed keeps them, and a doctor that has one is left as it is.
_CUENTA_SIN = 40          # 0..39: no account
_CUENTA_COMPLETA = 70     # 40..69: «Perfil completo»
_CUENTA_INGRESO = 85      # 70..84: logged in, profile not finished
_CUENTA_INVITADO = 95     # 85..94: invited; 95..99: «Sin invitar»


def _seed_doctor_accounts(agendas):
    from app.admin_panel import admin_users
    from app.auth import password_hashing
    import secrets
    t = sm.operators
    ph = None
    hechas = 0
    tienen = db_operators.operator_doctor_ids()
    for m in sorted(agendas.medicos(), key=lambda m: str(m.get("id"))):
        mid = m.get("id")
        h = _h("cuenta", mid)
        if not mid or mid == _MEDICO_DESTACADO or h < _CUENTA_SIN or str(mid) in tienen:
            continue
        # one hash for every account of the run: nobody logs in with these
        ph = ph or password_hashing.hash_password(secrets.token_urlsafe(32))
        try:
            with db._connect() as conn:  # noqa: SLF001
                # the name check and the create share one transaction, as in
                # `admin_users.provision_doctor_account`
                usuario = admin_users._available_username(  # noqa: SLF001
                    admin_users.doctor_username_base(m), cur=conn.cursor())
                db_operators.operator_create(usuario, rol="medico", medico_id=str(mid), pass_hash=ph,
                                             activo=h < _CUENTA_INVITADO, connection=conn)
                conn.commit()
        except db_operators.OperatorConflict:
            continue  # a visitor took the name meanwhile: this doctor stays without one
        if h < _CUENTA_INVITADO:
            db_operators.operator_update(usuario, activo=True, invited=True)
        if h < _CUENTA_INGRESO:
            with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
                sqlcore.run(cur, sa.update(t).values(
                    last_access=_now() - timedelta(days=1 + h % 9, hours=h % 7)).where(
                    t.c.username == usuario))
                conn.commit()
        if h < _CUENTA_COMPLETA:
            db_operators.operator_complete_onboarding(usuario)
        hechas += 1
    print(f"  {hechas} cuentas de médico nuevas")


_QUERIES = [
    ("pediatria", 6), ("vacunas", 9), ("radiografia", 4), ("cardiologia", 7),
    ("laboratorio", 11), ("nutricion", 3), ("turno urgente", 0),
    ("ecografia", 5), ("medicina general", 8), ("vacunatorio", 6),
    ("hospital de sampleton", 2), ("riverside clinic", 4), ("turno pediatra", 5),
    ("analisis de sangre", 9), ("chequeo general", 3), ("nutricionista", 2),
    ("cardiologo", 6), ("vacuna antigripal", 4), ("rayos x", 3),
    ("consulta clinica", 7), ("guardia", 1), ("centro de salud", 5),
    ("turno con orden", 4), ("estudios", 6), ("consultorio", 2),
]


def _seed_searches():
    """`source` doubles as this script's marker (SEARCH_SOURCE) — the table
    has no separate column for the referring page, so the realistic-origin
    idea from searches.py's real callers doesn't survive here; only the query
    + result count do."""
    n = len(_QUERIES)
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        for i, (q, resultados) in enumerate(_QUERIES):
            ts = _now() - timedelta(days=2 + (i * 85 // n), hours=i % 20)
            sqlcore.run(cur, sa.insert(sm.searches).values(
                ts=ts, q=q, results=resultados, source=SEARCH_SOURCE))
        conn.commit()
    print(f"  {n} búsquedas creadas")


# The anonymous visit stream behind «Visitas» (analytics_events): the demo
# had 6 visits in 30 days next to a thousand patients with an appointment.
# Every visit is synthetic. Its event ids keep the stream's contract (23 hex
# digits, the first 13 the millisecond, so the export pages them), and its
# visit id `jid` starts with this prefix: a real jid is base36, never holds
# «-», so the next seed deletes exactly these rows.
TRAFICO_PREFIJO = "demo-"
_TRAFICO_DIAS = 35
# A day's requests made on the web, and how many visits end in one.
_TRAFICO_WEB = 0.55
_TRAFICO_CONVERSION = 0.18
_TRAFICO_MIN_VISITAS = 40
_TRAFICO_LOTE = 1500
_TRAFICO_ORIGENES = (("direct", 40), ("www.google.com", 34), ("lm.facebook.com", 12),
                     ("l.instagram.com", 8), ("web.whatsapp.com", 6))
_TRAFICO_EQUIPOS = (("mobile", 72), ("desktop", 23), ("tablet", 5))
_TRAFICO_IDIOMAS = (("es", 93), ("en", 4), ("pt", 3))


def _por_peso(opciones, x):
    """The option whose cumulative weight (out of 100) holds `x` in 0..99."""
    acum = 0
    for valor, peso in opciones:
        acum += peso
        if x < acum:
            return valor
    return opciones[-1][0]


def _seed_traffic():
    """Visits of the public site for the last _TRAFICO_DIAS days, sized by
    the requests the seed made each day: the web's share of them is the
    number of visits that reach «done», the rest of the funnel (landing,
    search, a doctor or service page, the slots, the submit) grows around it.
    Deterministic, like the rest of the seed."""
    from app.patient_web import catalog
    t, br = sm.analytics_events, sm.booking_requests
    ahora = _now()
    desde = (ahora - timedelta(days=_TRAFICO_DIAS - 1)).astimezone(ART).date()
    dia_de = sa.func.date(sa.func.timezone("America/Argentina/Buenos_Aires", br.c.created))
    servicios = [s.get("id") for s in catalog.services() or [] if s.get("id")]
    medicos = [m.get("id") for m in catalog.doctors() or [] if m.get("id")]
    centros = [c.get("slug") for c in catalog.centers() or [] if c.get("slug")]
    filas, total, visitas_total = [], 0, 0

    def evento(clave, ts, tipo, **campos):
        # A step that would land after now (a visit begun minutes ago) is not
        # written: the stream holds no event from the future.
        if ts > ahora:
            return
        ms = int(ts.timestamp() * 1000)
        filas.append(dict({c: None for c in ("route", "rid", "ref", "lang", "device", "jid", "pl",
                                              "q", "n", "origin", "step", "service")},
                          id=f"{ms:013x}{zlib.crc32(clave.encode()):010x}",
                          ts=ts, type=tipo, **campos))

    def volcar(cur):
        nonlocal filas, total
        for i in range(0, len(filas), _TRAFICO_LOTE):
            total += sqlcore.run(cur, _pg_insert(t).values(filas[i:i + _TRAFICO_LOTE])
                                 .on_conflict_do_nothing(index_elements=[t.c.id])).rowcount
        filas = []

    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        por_dia = {r["d"]: r["n"] for r in sqlcore.run(cur, sa.select(
            dia_de.label("d"), sa.func.count().label("n")).where(
            br.c.created >= datetime(desde.year, desde.month, desde.day, tzinfo=ART)).group_by(dia_de)).fetchall()}
        for k in range(_TRAFICO_DIAS):
            dia = desde + timedelta(days=k)
            hechas = round(por_dia.get(dia, 0) * _TRAFICO_WEB)
            visitas = max(_TRAFICO_MIN_VISITAS, round(hechas / _TRAFICO_CONVERSION))
            for v in range(visitas):
                x = zlib.crc32(f"visita|{dia}|{v}".encode())
                inicio = datetime(dia.year, dia.month, dia.day, 7 + x % 16, (x // 16) % 60,
                                  (x // 960) % 60, tzinfo=ART)
                if inicio > ahora:
                    continue
                jid = f"{TRAFICO_PREFIJO}{x:08x}{v:04x}"
                base = f"{dia:%Y%m%d}-{v}-"
                comun = {"lang": _por_peso(_TRAFICO_IDIOMAS, _h("idioma", dia, v)),
                         "device": _por_peso(_TRAFICO_EQUIPOS, _h("equipo", dia, v)), "jid": jid}
                entrada = _h("entrada", dia, v)
                ruta, rid = ("/", None) if entrada < 50 else ("/doctors", None) if entrada < 65 else (
                    ("/service/:id", servicios[x % len(servicios)]) if entrada < 85 and servicios else
                    ("/doctor/:id", medicos[x % len(medicos)]) if entrada < 97 and medicos else
                    ("/center/:slug", centros[x % len(centros)]) if centros else ("/", None))
                evento(base + "0", inicio, "page_view", route=ruta, rid=rid,
                       ref=_por_peso(_TRAFICO_ORIGENES, _h("origen", dia, v)), **comun)
                ts = inicio
                termina = v < hechas
                if _h("busca", dia, v) < 35:
                    q, resultados = _QUERIES[x % len(_QUERIES)]
                    ts += timedelta(seconds=20 + x % 40)
                    evento(base + "1", ts, "search", q=q, n=resultados, jid=jid)
                abre = termina or _h("abre", dia, v) < 45
                if abre and medicos:
                    ts += timedelta(seconds=30 + x % 90)
                    evento(base + "2", ts, "page_view", route="/doctor/:id",
                           rid=medicos[(x // 7) % len(medicos)], ref="internal", **comun)
                svc = servicios[(x // 11) % len(servicios)] if servicios else None
                if termina or (abre and _h("horarios", dia, v) < 55):
                    ts += timedelta(seconds=15 + x % 45)
                    evento(base + "3", ts, "step", step="slots_shown", service=svc, jid=jid)
                    if termina or _h("envia", dia, v) < 25:
                        ts += timedelta(seconds=60 + x % 120)
                        evento(base + "4", ts, "step", step="submit", service=svc, jid=jid)
                    if termina:
                        ts += timedelta(seconds=2 + x % 6)
                        evento(base + "5", ts, "step", step="done", service=svc, jid=jid)
                visitas_total += 1
            if len(filas) >= _TRAFICO_LOTE * 4:
                volcar(cur)
        volcar(cur)
        conn.commit()
    print(f"  {visitas_total} visitas anónimas del sitio en {_TRAFICO_DIAS} días ({total} eventos)")


_FOTOS_MEDICOS_DIR = os.path.join(ROOT, "packs", "example-town", "fotos-medicos")


# El retrato del paciente del recorrido va aparte: Sofía Herrera tiene 72 años
# y antes reusaba la foto de una médica, así que la ficha del paciente mostraba
# una profesional de guardapolvo.
_FOTO_PACIENTE = "paciente-demo.jpg"


def _seed_doctor_photos():
    """Copy the pack's fictional portraits into the runtime volume.

    Every file is named `<doctor-id>.jpg`, so the copy is a straight rename-free
    pass instead of the old hand-written 3-entry map — that map pointed at
    filenames (`perez-juan`) matching no catalog ID, so all 50 demo doctors fell
    back to initials. Regenerate the assets with
    `scripts/gen_demo_doctor_photos.py`; this runs on every demo seed, so the
    daily refresh (demo_refresh.py) restores them if the volume is wiped.
    """
    if not os.path.isdir(_FOTOS_MEDICOS_DIR):
        return
    os.makedirs(doctor_photos.PHOTOS_DIR, exist_ok=True)
    copiadas = 0
    for nombre in sorted(os.listdir(_FOTOS_MEDICOS_DIR)):
        if not nombre.endswith(".jpg") or nombre == _FOTO_PACIENTE:
            continue
        shutil.copyfile(
            os.path.join(_FOTOS_MEDICOS_DIR, nombre),
            os.path.join(doctor_photos.PHOTOS_DIR, nombre),
        )
        copiadas += 1
    os.makedirs(account_photo.PHOTOS_DIR, exist_ok=True)
    patient_source = os.path.join(_FOTOS_MEDICOS_DIR, _FOTO_PACIENTE)
    pacientes = 0
    if os.path.isfile(patient_source):
        shutil.copyfile(
            patient_source,
            os.path.join(account_photo.PHOTOS_DIR, DEMO_PATIENT["dni"] + ".jpg"),
        )
        pacientes = 1
    print(f"  {copiadas} fotos de médicos y {pacientes} foto de paciente copiadas")


def _seed_language_config():
    """The demo exposes every shipped language in shared chrome and lets the
    browser's Accept-Language pick the first one (2026-09-11: no explicit
    LANG_DEFAULT — an Italian visitor arriving from humanrounds.org's
    Italian page, or straight from an Italian browser, reads the demo in
    Italian; an explicit default would outrank the browser and pin
    everyone to Spanish). Order is the shipped one, `es` first, so a
    browser with no usable preference still lands on the pack's own
    language."""
    from app.core import i18n
    codes = [c for c in i18n.LANGUAGE_NAMES if c in i18n.available_locales()]
    config.guardar({"LANGUAGES": ",".join(codes), "LANG_DEFAULT": "",
                    "LANG_SWITCHER_MODE": "nav"})
    print(f"  idioma: {','.join(codes)} habilitados, default por Accept-Language, switcher visible")


def seed_assets():
    """Copy the pack's demo IMAGES into the runtime volume, and nothing else.

    Split out of seed() because the two have opposite cadences. seed() is a
    delete-then-recreate of the day's fictional rows and only runs when the
    calendar day changes (demo_refresh.py), so a deploy that ships new pack
    assets on an already-seeded day left them invisible until the next
    midnight — that is how the 50 demo portraits landed on 2026-08-04 and the
    demo still showed «+8» circles with no faces. Copying files is idempotent
    and touches no visitor state, so this half runs on every boot instead."""
    if not _guard("seed_assets"):
        return
    _seed_doctor_photos()


def seed(force=False):
    """Marker-based delete-then-recreate — idempotent, safe to run daily
    (Coolify's scheduled task) or by hand. Hard no-op unless
    demo_installation.enabled().

    `force=False` (the default, and what Coolify's post_deployment_command
    calls) skips the actual delete-then-recreate when `dia_cubierto()` says
    today's data is already there — a same-day redeploy still refreshes the
    pack's photos and language config (cheap, and the whole point of
    running this on every boot per seed_assets()'s docstring), but doesn't
    re-touch 200+ rows nobody asked to change. `force=True` (`--force` on
    the CLI, or demo_refresh.py's own daily rollover) always reseeds."""
    if not _guard("seed"):
        return
    demo_installation.apply_safety_defaults()
    _seed_language_config()
    _seed_doctor_photos()
    # The catalog overlay is asserted on EVERY call, before the day-coverage
    # check: an already-seeded day still gets the demo's every-day service and
    # the doctor's symptom capture the moment this code ships (the queue keeps
    # its old slots until the midnight reseed; the extra hours are simply free).
    # Never worth the day's data: a failed overlay leaves the pack's own hours
    # and the keep-alive retries it within five minutes.
    try:
        if _asegurar_catalogo_demo():
            print("  catálogo de la demo actualizado: el servicio demo atiende "
                  "todos los días de 8 a 20, entrevista habilitada para su médico")
    except Exception as exc:  # noqa: BLE001 — the seed goes on with the pack's hours
        print(f"  catálogo de la demo sin actualizar ({type(exc).__name__}); "
              "se reintenta en el próximo ciclo")
    if not force and dia_cubierto():
        print("demo seed: el día ya estaba sembrado; sólo se actualizaron "
              "fotos e idioma.")
        return
    with db._connect() as conn, conn.cursor() as cur:  # noqa: SLF001
        _delete_marked(cur)
        conn.commit()
    _seed_patient_accounts()
    # One ledger of every demo doctor's slots for the whole seed: the
    # featured patient's appointment is reserved first, today's queue fills
    # around it, the history takes the free slots of past days.
    agendas = AgendaDemo()
    proximo = _turno_destacado(agendas)
    _seed_today_queue(agendas)
    _seed_history(agendas)
    _seed_featured_patient(agendas, proximo)
    if LLENAR_AGENDAS:
        _seed_agendas(agendas)
        _seed_doctor_accounts(agendas)
    _seed_searches()
    _seed_traffic()
    print("demo seed: listo.")


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "seed"
    if cmd == "reset":
        reset()
    elif cmd == "seed":
        seed()
    else:
        print(f"uso: {sys.argv[0]} [seed|reset]", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
