"""test_desk_doctor_pool.py — «Crear turno» y la cola hablan del MISMO pool de médicos (2026-10-23).

Hospital map desk.json d10 / problems.md №2 (fix-desk, K2): el vínculo
médico ↔ servicio tiene UNA sola regla, `schedule_policy.belongs` (etiqueta de la
franja, o cluster + centro, y la especialidad en los clusters comodín `otras` /
`diagnostico` / `grupos`). Es la regla con la que ofrece la web, bloquea el
calendario del médico y asigna la cola; el modal «Crear turno» y la carga de la
mesa la repetían a mano («cluster + centro») y en un cluster comodín le ponían
a un dermatólogo los servicios de traumatología.

Lo que este archivo fija:
  - la lista de cada médico (`_servicios_pool`), el pool por servicio de la cola
    (`medicos_por_servicio`), los médicos con nombre de la grilla de la mesa y el
    calendario del médico (`managed_services`) dicen lo mismo — sin que se
    estreche `belongs` (decisions 2026-10-14);
  - un médico fuera del pool no ofrece horarios ni se carga: 409
    `medico_no_atiende_servicio` (la cola ya devuelve el mismo código);
  - un servicio ajeno a la agenda del médico es 404, no 403.

Correr: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_desk_doctor_pool.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.admin_panel import admin_appointments, admin_appointments_router, admin_content  # noqa: E402
from app.core import tz  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(admin_appointments_router.router)
_CLIENT = TestClient(_app)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_desk_pool", seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_FRANJA = {"dias": [1, 2, 3, 4, 5], "desde": "09:00", "hasta": "12:00", "cada_min": 20}


def _svc(sid, nombre, cluster, **extra):
    return dict({"id": sid, "nombre": nombre, "centro": "hospital", "cluster": cluster,
                 "categoria": "Consultorios", "modo": "solicitud"}, **extra)


def _doc(did, nombre, especialidad, cluster, franjas=None, centro="hospital"):
    return {"id": did, "nombre": nombre, "especialidad": especialidad, "cluster": cluster,
            "centro": centro,
            "centros": [{"centro": centro, "franjas": franjas if franjas is not None else [dict(_FRANJA)]}]}


# `otras` is a catch-all cluster: traumatología and dermatología share it but are
# different specialties. Cardiología is a normal cluster with its own schedule.
_SERVICIOS = {"servicios": [
    _svc("trauma-hosp", "Traumatología", "otras"),
    _svc("derma-hosp", "Dermatología", "otras"),
    _svc("cardio-hosp", "Cardiología", "cardiologia", agenda=dict(_FRANJA)),
    _svc("eco-hosp", "Ecocardiografía", "diagnostico"),
]}
_CENTROS = {"centros": [{"slug": "hospital", "name": "Hospital", "address": "Av. 1"},
                        {"slug": "caps", "name": "CAPS", "address": "Av. 2"}]}
_MEDICOS = {"medicos": [
    _doc("tomas", "Tomas Trauma", "Traumatólogo", "otras"),
    _doc("dora", "Dora Derma", "Dermatóloga", "otras"),
    _doc("cora", "Cora Cardio", "Cardióloga", "cardiologia"),
    # cardiologist at ANOTHER center: not in the hospital's cardiology pool
    _doc("caps-cardio", "Cesar Caps", "Cardiólogo", "cardiologia", centro="caps"),
    # a traumatologist whose window is tagged for the echo service: the tag
    # outranks every inference (decisions 2026-10-09)
    _doc("tag", "Tati Etiquetada", "Traumatóloga", "otras",
         franjas=[dict(_FRANJA, servicio="eco-hosp")]),
]}


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    for name, doc in (("servicios.json", _SERVICIOS), ("centros-salud.json", _CENTROS),
                      ("medicos.json", _MEDICOS)):
        with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
            json.dump(doc, f)


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


def _un_dia_habil():
    d = datetime.now(tz.tzinfo()).date() + timedelta(days=1)
    while d.isoweekday() > 5:
        d += timedelta(days=1)
    return d.isoformat()


class DeskDoctorPoolTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests, users, catalog, slot_holds RESTART IDENTITY CASCADE")
        self._tmp = tempfile.mkdtemp(prefix="desk-pool-test-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        self.addCleanup(catalog._JSON_CACHE.clear)
        self.addCleanup(catalog.invalidate)
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        from app.modules.turnos import slot_holds
        slot_holds._TAKEN_CACHE["ts"] = 0
        db_accounts.create_user("30111222", "Sofía", "Herrera", "1953-08-11", "F",
                       "2254550001", "secreto123", email="sofia@example.com")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _crear(self, body, headers=None):
        return _CLIENT.request("POST", "/api/admin/appointments", json=body,
                               headers=headers or _admin_headers())

    def _slots(self, servicio_id, medico=None, headers=None):
        from urllib.parse import quote
        path = "/api/admin/appointment-slots?servicio_id=" + quote(servicio_id)
        if medico:
            path += "&medico=" + quote(medico)
        return _CLIENT.request("GET", path, headers=headers or _admin_headers())

    # ---- one rule, four readers ---------------------------------------------

    def _pool_rule(self):
        """The reference: `belongs` over the catalog, minus deleted/role rows."""
        from app.modules.turnos import schedule_policy
        medicos, servicios = catalog.doctors(), catalog.services()
        return {s["id"]: sorted(m["id"] for m in medicos if schedule_policy.belongs(m, s))
                for s in servicios}

    def test_el_pool_esperado_del_fixture(self):
        # the catch-all specialty clause and the tag, spelled out. The pool is
        # broad (decisions 2026-10-14), but a doctor whose windows at the
        # center ALL name another service leaves the pool of a service with
        # no hours of its own while someone stays (decisions 2026-10-24, reference-deployment counterpart:
        # Berisso does only Ergometría): Tati has no trauma minutes anyway, so
        # calendar, capacity and offers still agree.
        self.assertEqual(self._pool_rule(), {
            "trauma-hosp": ["tomas"],            # Dora (dermatología) NOT in; Tati only echo
            "derma-hosp": ["dora"],
            "cardio-hosp": ["cora"],             # Cesar is at another center
            "eco-hosp": ["tag"],                 # the tag puts a traumatologist in echo
        })

    def test_la_cola_el_modal_el_calendario_y_la_grilla_dicen_lo_mismo(self):
        from app.modules.turnos import booking_queue, schedule_policy
        esperado = self._pool_rule()
        # 1. the queue: medicos_por_servicio
        cola = {sid: sorted(ids) for sid, ids in booking_queue._doctor_pool_by_service().items()}
        self.assertEqual({k: cola[k] for k in esperado}, esperado)
        # 2. «Crear turno» / the doctor list: each doctor's `_servicios_pool`
        listado = admin_content.list_payload("medicos")["items"]
        por_servicio = {sid: [] for sid in esperado}
        for m in listado:
            for sid in m["_servicios_pool"]:
                por_servicio[sid].append(m["id"])
        self.assertEqual({k: sorted(v) for k, v in por_servicio.items()}, esperado)
        # 3. the doctor's calendar / capacity / read-only form: managed_services
        #    (services WITH their own schedule; cardiología here)
        servicios = catalog.services()
        for m in catalog.doctors():
            gestionados = {s["id"] for s in schedule_policy.managed_services(m, servicios)}
            self.assertEqual(gestionados, {sid for sid in ("cardio-hosp",) if m["id"] in esperado[sid]}, m["id"])
        # 4. the desk grid: the doctors it names for a service without its own
        #    schedule are the pool's doctors that HAVE minutes there — Tati's
        #    only window is tagged for echo, so she has no trauma minutes and,
        #    with Tomas staying, is out of the trauma pool
        con_horarios = {"trauma-hosp": ["tomas"], "derma-hosp": ["dora"], "eco-hosp": ["tag"]}
        for sid, quienes in con_horarios.items():
            servicio = next(s for s in servicios if s["id"] == sid)
            grid = admin_appointments._desk_slots_by_doctor(servicio)
            nombres = sorted({b["medico_id"] for b in grid.get("medicos") or []})
            self.assertEqual(nombres, quienes, sid)
            self.assertTrue(set(nombres) <= set(esperado[sid]), sid)

    def test_el_horario_de_un_medico_fijado_sigue_al_pool(self):
        # offers for a pinned doctor exist iff the doctor is in the service's pool
        # (service, doctor, has minutes, in the pool)
        for sid, medico, hay, en_pool in (
                ("trauma-hosp", "Tomas Trauma", True, True),
                ("trauma-hosp", "Dora Derma", False, False),
                ("derma-hosp", "Dora Derma", True, True),
                ("derma-hosp", "Tomas Trauma", False, False),
                ("eco-hosp", "Tati Etiquetada", True, True),
                # her only window is tagged for echo and Tomas stays in the
                # trauma pool, so she is out of it (decisions 2026-10-24) — the
                # other traumatologists' minutes never stand in for hers
                ("trauma-hosp", "Tati Etiquetada", False, False),
                ("cardio-hosp", "Cesar Caps", False, False)):
            h = self._slots(sid, medico)
            self.assertEqual(h.status_code, 200, h.json())
            self.assertEqual(bool(h.json()["libres"]), hay, (sid, medico, h.json()))
            self.assertEqual(h.json().get("motivo") == "medico_no_atiende_servicio", not en_pool,
                             (sid, medico, h.json()))
            if not en_pool:
                # no minutes and no overbooking capacity either
                self.assertEqual((h.json()["libres"], h.json()["sobreturnos"]), ([], []))

    def test_los_minutos_de_la_mesa_son_los_del_medico_en_la_web(self):
        # the desk offers exactly the minutes the patient-side engine gives that
        # doctor (the desk only widens the horizon): same relation, same minutes
        from app.modules.turnos import booking_horizon
        from app.patient_web import site_availability
        for sid, medico, did in (("trauma-hosp", "Tomas Trauma", "tomas"),
                                 ("derma-hosp", "Dora Derma", "dora"),
                                 ("eco-hosp", "Tati Etiquetada", "tag")):
            servicio = next(s for s in catalog.services() if s["id"] == sid)
            with booking_horizon.staff():
                esperado = {(t["dia"], t["hora"])
                            for b in site_availability._appointments_for_doctor(servicio, did).get("medicos") or []
                            for t in b["turnos"]}
            desk = {(t["dia"], t["hora"]) for t in self._slots(sid, medico).json()["libres"]}
            self.assertTrue(esperado, sid)
            self.assertEqual(desk, esperado, sid)

    # ---- the desk does not book a doctor of another specialty ---------------

    def test_la_mesa_no_carga_a_un_medico_de_otra_especialidad(self):
        h = self._crear({"dni": "30111222", "servicio_id": "trauma-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20", "medico": "Dora Derma"})
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "medico_no_atiende_servicio")
        with db._connect() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM booking_requests")
            self.assertEqual(cur.fetchone()["n"], 0)

    def test_un_nombre_que_no_es_del_catalogo_tampoco_se_carga(self):
        h = self._crear({"dni": "30111222", "servicio_id": "trauma-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20", "medico": "Nadie Conocido"})
        self.assertEqual(h.status_code, 409, h.json())
        self.assertEqual(h.json()["error"], "medico_no_atiende_servicio")

    def test_el_medico_del_pool_si_se_carga_con_su_id(self):
        h = self._crear({"dni": "30111222", "servicio_id": "trauma-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20", "medico": "Tomas Trauma"})
        self.assertEqual(h.status_code, 200, h.json())
        fila = db_booking.get_booking_request(h.json()["id"])
        self.assertEqual((fila["medico_asignado"], fila["medico_asignado_id"]), ("Tomas Trauma", "tomas"))

    def test_sin_medico_se_sigue_cargando(self):
        h = self._crear({"dni": "30111222", "servicio_id": "trauma-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"})
        self.assertEqual(h.status_code, 200, h.json())

    # ---- a doctor session: foreign is 404, never 403 ------------------------

    def _medico(self, usuario, medico_id):
        with db._connect() as c, c.cursor() as cur:
            cur.execute("DELETE FROM operators WHERE username=%s", (usuario,))
        db_operators.operator_create(usuario, "clave1234", "medico", medico_id=medico_id)
        return _cookie(usuario, "medico")

    def test_un_medico_no_carga_un_servicio_de_otra_especialidad_aunque_comparta_cluster(self):
        # Dora and Tomas share the cluster `otras` and the center: the old
        # «cluster + centro» check let Dora give a traumatology turno.
        h = self._crear({"dni": "30111222", "servicio_id": "trauma-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"},
                        headers=self._medico("dra-dora", "dora"))
        self.assertEqual(h.status_code, 404, h.json())
        self.assertIn("tu agenda", h.json()["error"])

    def test_un_medico_carga_en_su_propio_servicio(self):
        h = self._crear({"dni": "30111222", "servicio_id": "derma-hosp",
                         "dia": _un_dia_habil(), "hora": "09:20"},
                        headers=self._medico("dra-dora", "dora"))
        self.assertEqual(h.status_code, 200, h.json())

    # ---- a named doctor's turno lands inside their agenda (launch plan P12) --
    # The queue's approval already checks it (`_doctor_appointment_in_schedule`);
    # «Crear turno» did not: a stale screen or a hand-made request gave Tomas a
    # turno at 13:00, on a Saturday, at 09:10 — times his agenda never had.

    def _sin_turnos(self):
        with db._connect() as c, c.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM booking_requests")
            self.assertEqual(cur.fetchone()["n"], 0)

    def test_la_mesa_no_carga_fuera_de_la_agenda_del_medico(self):
        sabado = datetime.fromisoformat(_un_dia_habil()).date()
        while sabado.isoweekday() != 6:
            sabado += timedelta(days=1)
        for dia, hora in ((_un_dia_habil(), "13:00"),      # after his window
                          (_un_dia_habil(), "09:10"),      # inside, off his 20-minute grid
                          (sabado.isoformat(), "09:20")):  # a day he does not work
            h = self._crear({"dni": "30111222", "servicio_id": "trauma-hosp",
                             "dia": dia, "hora": hora, "medico": "Tomas Trauma"})
            self.assertEqual(h.status_code, 409, (dia, hora, h.json()))
            self.assertEqual(h.json()["error"], "turno_medico_fuera_de_agenda", (dia, hora))
        self._sin_turnos()

    def test_fuera_de_la_ventana_solo_el_propio_medico(self):
        # «booking outside the window — only the doctor themself» (the product owner, Codex 21.10 item 14):
        # the desk naming Dora at 13:00 is refused; Dora herself, on her own
        # session, gives that turno.
        mesa = self._crear({"dni": "30111222", "servicio_id": "derma-hosp",
                            "dia": _un_dia_habil(), "hora": "13:00", "medico": "Dora Derma"})
        self.assertEqual(mesa.status_code, 409, mesa.json())
        self.assertEqual(mesa.json()["error"], "turno_medico_fuera_de_agenda")
        self._sin_turnos()
        propio = self._crear({"dni": "30111222", "servicio_id": "derma-hosp",
                              "dia": _un_dia_habil(), "hora": "13:00"},
                             headers=self._medico("dra-dora", "dora"))
        self.assertEqual(propio.status_code, 200, propio.json())

    def test_un_servicio_con_agenda_propia_mide_con_la_agenda_del_servicio(self):
        # Cardiología owns its schedule: Cora's minutes there are the service's
        dentro = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                              "dia": _un_dia_habil(), "hora": "09:20", "medico": "Cora Cardio"})
        self.assertEqual(dentro.status_code, 200, dentro.json())
        fuera = self._crear({"dni": "30111222", "servicio_id": "cardio-hosp",
                             "dia": _un_dia_habil(), "hora": "12:20", "medico": "Cora Cardio"})
        self.assertEqual(fuera.status_code, 409, fuera.json())
        self.assertEqual(fuera.json()["error"], "turno_medico_fuera_de_agenda")


if __name__ == "__main__":
    unittest.main()
