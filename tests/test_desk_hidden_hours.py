"""test_desk_hidden_hours.py — «Crear turno» shows the hours hidden from the web (2026-11-11).

A band of a doctor's agenda with `cupos.web: 0` is closed to the patient's
site but is exactly the time the desk and the doctor keep for phone bookings
and their own patients. «Pacientes de hoy» already shows those hours free; the
«Crear turno» grid read the site-filtered day and hid them, so the desk could
not book there. What this file pins:
  - the desk grid (`/api/admin/appointment-slots`) lists the web:0 hours;
  - a turno can be booked on one of them from «Crear turno»;
  - the patient's site / wizard (`_appointments_for_doctor` outside the desk)
    still does not offer them.

Run: TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p test_desk_hidden_hours.py
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

_db = _dbtest.PerModuleDb("sana_turnos_test_desk_hidden", seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_DIAS = [1, 2, 3, 4, 5]
_OPEN = {"dias": _DIAS, "desde": "09:00", "hasta": "10:00", "cada_min": 20}
_HIDDEN = {"dias": _DIAS, "desde": "10:00", "hasta": "11:00", "cada_min": 20, "cupos": {"web": 0}}
_HIDDEN_HOURS = ["10:00", "10:20", "10:40"]

_SERVICIOS = {"servicios": [{"id": "trauma-hosp", "nombre": "Traumatología", "centro": "hospital",
                             "cluster": "traumatologia", "categoria": "Consultorios",
                             "modo": "solicitud"}]}
_CENTROS = {"centros": [{"slug": "hospital", "name": "Hospital", "address": "Av. 1"}]}
_MEDICOS = {"medicos": [{"id": "tomas", "nombre": "Tomas Trauma", "especialidad": "Traumatólogo",
                         "cluster": "traumatologia", "centro": "hospital",
                         "centros": [{"centro": "hospital", "franjas": [dict(_OPEN), dict(_HIDDEN)]}]}]}


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    for name, doc in (("servicios.json", _SERVICIOS), ("centros-salud.json", _CENTROS),
                      ("medicos.json", _MEDICOS)):
        with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
            json.dump(doc, f)


def _admin_headers():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


class DeskHiddenHoursTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests, users, catalog, slot_holds RESTART IDENTITY CASCADE")
        self._tmp = tempfile.mkdtemp(prefix="desk-hidden-test-")
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

    def _desk(self, **extra):
        path = "/api/admin/appointment-slots?servicio_id=trauma-hosp&medico=Tomas%20Trauma"
        h = _CLIENT.request("GET", path, headers=_admin_headers())
        self.assertEqual(h.status_code, 200, h.json())
        return h.json()["libres"]

    def test_la_grilla_de_la_mesa_incluye_las_horas_web_cero(self):
        horas = {t["hora"] for t in self._desk()}
        self.assertTrue(set(_HIDDEN_HOURS) <= horas, sorted(horas))
        self.assertIn("09:00", horas)

    def test_la_web_del_paciente_no_las_ofrece(self):
        from app.patient_web import site_availability
        servicio = catalog.service_by_id("trauma-hosp") if hasattr(catalog, "service_by_id") else None
        servicio = servicio or _SERVICIOS["servicios"][0]
        data = site_availability._appointments_for_doctor(servicio, "Tomas Trauma")
        horas = {t["hora"] for m in data.get("medicos") or [] for t in m["turnos"]}
        self.assertIn("09:00", horas)
        self.assertFalse(set(_HIDDEN_HOURS) & horas, sorted(horas))

    def test_se_puede_cargar_un_turno_en_una_hora_web_cero(self):
        t = next(t for t in self._desk() if t["hora"] == "10:20")
        h = _CLIENT.request("POST", "/api/admin/appointments", headers=_admin_headers(), json={
            "dni": "30111222", "servicio_id": "trauma-hosp", "dia": t["dia"], "hora": "10:20",
            "medico": "Tomas Trauma"})
        self.assertEqual(h.status_code, 200, h.json())
        self.assertNotIn("10:20", {x["hora"] for x in self._desk() if x["dia"] == t["dia"]})


if __name__ == "__main__":
    unittest.main()
