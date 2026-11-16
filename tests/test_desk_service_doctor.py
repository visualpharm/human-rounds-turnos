"""test_desk_service_doctor.py — «Crear turno» asks for the doctor when the minutes have none (2026-10-24).

Launch plan P1 (hospital map, desk d23: «Doctor not assigned — Busy 10»): a
service that owns its schedule (`agenda` on the service) offers minutes that
belong to no doctor in particular — `_appointments_for_service` merges them
anonymously. The desk booked them as they came, with no doctor, and the turno
landed in «Médico no asignado» even when the service has two doctors.

What this file pins:
  - unnamed minutes come with the service's doctor pool (`medicos`, the same
    `_doctor_pool` the queue's approval offers) for the desk to choose from;
  - a lab never gets that list (a lab assigns no doctor, product owner 2026-07-19);
  - a doctor pinned by the caller, or minutes that already name a doctor, get no list;
  - the doctor chosen there is the one the turno is booked with.

Run: TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p test_desk_service_doctor.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.admin_panel import admin_appointments_router  # noqa: E402
from app.core import tz  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(admin_appointments_router.router)
_CLIENT = TestClient(_app)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_desk_svc_doc", seed=lambda: db_booking.get_booking_request(-1))


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


def _doc(did, nombre, especialidad, cluster, franjas=None, **extra):
    return dict({"id": did, "nombre": nombre, "especialidad": especialidad, "cluster": cluster,
                 "centro": "hospital",
                 "centros": [{"centro": "hospital", "franjas": franjas or []}]}, **extra)


_SERVICIOS = {"servicios": [
    # owns its schedule; two kinesiologists share it
    _svc("kine-hosp", "Kinesiología", "kinesiologia", agenda=dict(_FRANJA)),
    # a lab owning its schedule, with a doctor in its cluster all the same
    _svc("lab-hosp", "Laboratorio", "laboratorio", agenda=dict(_FRANJA)),
    # a service whose minutes come from a doctor's own windows
    _svc("trauma-hosp", "Traumatología", "traumatologia"),
]}
_CENTROS = {"centros": [{"slug": "hospital", "name": "Hospital", "address": "Av. 1"}]}
_MEDICOS = {"medicos": [
    _doc("kara", "Kara Kine", "Kinesióloga", "kinesiologia"),
    _doc("kevin", "Kevin Kine", "Kinesiólogo", "kinesiologia"),
    _doc("tecnica", "Técnica Lab", "Técnica", "laboratorio", rol="Técnico/a"),
    _doc("bio", "Beto Bioquímico", "Bioquímico", "laboratorio"),
    _doc("tomas", "Tomas Trauma", "Traumatólogo", "traumatologia", franjas=[dict(_FRANJA)]),
]}


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    for name, doc in (("servicios.json", _SERVICIOS), ("centros-salud.json", _CENTROS),
                      ("medicos.json", _MEDICOS)):
        with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
            json.dump(doc, f)


def _admin_headers():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


def _un_dia_habil():
    d = datetime.now(tz.tzinfo()).date() + timedelta(days=1)
    while d.isoweekday() > 5:
        d += timedelta(days=1)
    return d.isoformat()


class DeskServiceDoctorTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests, users, catalog, slot_holds RESTART IDENTITY CASCADE")
        self._tmp = tempfile.mkdtemp(prefix="desk-svc-doc-test-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        from app.modules.turnos import slot_holds
        slot_holds._TAKEN_CACHE["ts"] = 0
        db_accounts.create_user("30111222", "Sofía", "Herrera", "1953-08-11", "F",
                       "2254550001", "secreto123", email="sofia@example.com")

    def tearDown(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _slots(self, servicio_id, medico=None):
        from urllib.parse import quote
        path = "/api/admin/appointment-slots?servicio_id=" + quote(servicio_id)
        if medico:
            path += "&medico=" + quote(medico)
        h = _CLIENT.request("GET", path, headers=_admin_headers())
        self.assertEqual(h.status_code, 200, h.json())
        return h.json()

    def test_minutos_sin_medico_traen_el_pool_del_servicio(self):
        j = self._slots("kine-hosp")
        self.assertTrue(j["libres"])
        self.assertEqual({t["medico"] for t in j["libres"]}, {""}, "the service's own minutes name nobody")
        self.assertEqual(sorted(j.get("medicos") or []), ["Kara Kine", "Kevin Kine"])

    def test_un_laboratorio_no_pide_medico(self):
        j = self._slots("lab-hosp")
        self.assertTrue(j["libres"])
        self.assertEqual(j.get("medicos") or [], [])

    def test_con_medico_fijado_o_minutos_con_nombre_no_hay_lista(self):
        self.assertNotIn("medicos", self._slots("kine-hosp", "Kara Kine"))
        j = self._slots("trauma-hosp")
        self.assertEqual({t["medico"] for t in j["libres"]}, {"Tomas Trauma"})
        self.assertNotIn("medicos", j)

    def test_el_medico_elegido_es_el_del_turno(self):
        j = self._slots("kine-hosp")
        t = j["libres"][0]
        h = _CLIENT.request("POST", "/api/admin/appointments", headers=_admin_headers(), json={
            "dni": "30111222", "servicio_id": "kine-hosp", "dia": t["dia"], "hora": t["hora"],
            "medico": j["medicos"][1]})
        self.assertEqual(h.status_code, 200, h.json())
        self.assertEqual(db_booking.get_booking_request(h.json()["id"])["medico_asignado"], j["medicos"][1])


if __name__ == "__main__":
    unittest.main()
