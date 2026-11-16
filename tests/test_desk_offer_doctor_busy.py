"""test_desk_offer_doctor_busy.py — the time the desk offers for a doctor is a time
the approval accepts for that doctor (forms-audit 2026-10-25, slot-409).

In Solicitudes the «Elegí un horario» select comes from
`GET /api/admin/appointment-slots?servicio_id=&medico=` (falling back to the
service's grid when the doctor has nothing of their own), and «Aprobar» posts the
chosen minute with that same doctor. The approval refuses a minute the doctor
already has (`409 turno_medico_tomado`), so the offer must never contain one:
not right after the previous approval took it, and not when the doctor took it in
another service of the same pool.

Correr: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_desk_offer_doctor_busy.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from urllib.parse import quote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.admin_panel import admin_appointments_router  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_app = FastAPI()
_app.include_router(admin_appointments_router.router)
_CLIENT = TestClient(_app)

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_desk_offer_busy", seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_FRANJA = {"dias": [1, 2, 3, 4, 5], "desde": "07:00", "hasta": "10:00", "cada_min": 15}
_CENTRO = "hospital"
_MEDICO = "Cora Cardio"


def _svc(sid, nombre, **extra):
    return dict({"id": sid, "nombre": nombre, "centro": _CENTRO, "cluster": "cardiologia",
                 "categoria": "estudios", "modo": "solicitud_con_orden",
                 "agenda": dict(_FRANJA)}, **extra)


# Two services of the same pool, each with its own agenda (electrocardiograma-hospital
# in the example pack is one of these): the cardiologist is assigned to both.
_SERVICIOS = {"servicios": [_svc("ecg-hosp", "Electrocardiograma"),
                            _svc("ergo-hosp", "Ergometría"),
                            {k: v for k, v in _svc("consulta-hosp", "Cardiología").items() if k != "agenda"}]}
_CENTROS = {"centros": [{"slug": _CENTRO, "name": "Hospital", "address": "Av. 1"}]}
_MEDICOS = {"medicos": [{"id": "cora", "nombre": _MEDICO, "especialidad": "Cardióloga",
                         "cluster": "cardiologia", "centro": _CENTRO,
                         "centros": [{"centro": _CENTRO, "franjas": [dict(_FRANJA)]}]},
                        # no windows of his own: Solicitudes falls back to the service's grid
                        {"id": "nico", "nombre": "Nico Sin Agenda", "especialidad": "Cardiólogo",
                         "cluster": "cardiologia", "centro": _CENTRO,
                         "centros": [{"centro": _CENTRO, "franjas": []}]}]}


def _write_fixtures(webdir):
    os.makedirs(webdir, exist_ok=True)
    for name, doc in (("servicios.json", _SERVICIOS), ("centros-salud.json", _CENTROS),
                      ("medicos.json", _MEDICOS)):
        with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
            json.dump(doc, f)


def _admin_headers():
    return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}


class DeskOfferDoctorBusyTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests, users, catalog, slot_holds RESTART IDENTITY CASCADE")
        self._tmp = tempfile.mkdtemp(prefix="desk-offer-busy-")
        _write_fixtures(os.path.join(self._tmp, "webdata"))
        _env.set_env(self, TURNOS_WEBDATA_DIR=os.path.join(self._tmp, "webdata"))
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        from app.modules.turnos import slot_holds
        slot_holds.invalidate()

    def tearDown(self):
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _request(self, servicio_id, dni):
        return db_booking.create_booking_request(dni, "Pide", "Turno", "1990-01-01", None, servicio_id, _CENTRO)

    def _offered(self, servicio_id, medico=_MEDICO):
        """The minute the Solicitudes select pre-picks for the doctor (`slotsPara`):
        the doctor's own list, or the service's grid when that one is empty."""
        path = ("/api/admin/appointment-slots?servicio_id=" + quote(servicio_id)
                + "&medico=" + quote(medico) + "&respaldo=servicio")
        libres = _CLIENT.get(path, headers=_admin_headers()).json().get("libres")
        self.assertTrue(libres, servicio_id)
        return libres[0]["dia"], libres[0]["hora"]

    def _approve(self, sid, dia, hora, medico=_MEDICO):
        from app.modules.turnos import booking_decisions
        body = {"turno_dt": f"{dia}T{hora}:00-03:00", "medico": medico}
        return booking_decisions._approve_core({"usuario": "admin", "rol": "admin"},
                                              db_booking.get_booking_request(sid), sid, body)

    def test_la_hora_ofrecida_justo_despues_de_aprobar_se_puede_aprobar(self):
        primera = self._offered("ecg-hosp")
        self.assertEqual(self._approve(self._request("ecg-hosp", "30999501"), *primera)[0], 200)
        segunda = self._offered("ecg-hosp")
        self.assertNotEqual(segunda, primera)
        status, body = self._approve(self._request("ecg-hosp", "30999502"), *segunda)
        self.assertEqual(status, 200, body)

    def test_la_hora_que_el_medico_tiene_en_otro_servicio_no_se_ofrece(self):
        tomada = self._offered("ergo-hosp")
        self.assertEqual(self._approve(self._request("ergo-hosp", "30999503"), *tomada)[0], 200)
        ofrecida = self._offered("ecg-hosp")
        self.assertNotEqual(ofrecida, tomada)
        status, body = self._approve(self._request("ecg-hosp", "30999504"), *ofrecida)
        self.assertEqual(status, 200, body)

    def test_medico_sin_ventanas_propias_tampoco_se_le_ofrece_lo_que_ya_tiene(self):
        medico = "Nico Sin Agenda"
        tomada = self._offered("ergo-hosp", medico)
        self.assertEqual(self._approve(self._request("ergo-hosp", "30999505"), *tomada, medico=medico)[0], 200)
        ofrecida = self._offered("ecg-hosp", medico)
        self.assertNotEqual(ofrecida, tomada)
        status, body = self._approve(self._request("ecg-hosp", "30999506"), *ofrecida, medico=medico)
        self.assertEqual(status, 200, body)

    def test_respaldo_servicio_da_la_grilla_del_servicio_sin_lo_del_medico(self):
        # consulta-hosp has no agenda of its own: its grid is Cora's windows, and
        # Nico has none there — `respaldo=servicio` is what Solicitudes asks for
        base = "/api/admin/appointment-slots?servicio_id=consulta-hosp&medico=" + quote("Nico Sin Agenda")
        self.assertEqual(_CLIENT.get(base, headers=_admin_headers()).json().get("libres"), [])
        grilla = _CLIENT.get(base + "&respaldo=servicio", headers=_admin_headers()).json().get("libres")
        self.assertTrue(grilla)
        self.assertEqual({t["medico"] for t in grilla}, {"Nico Sin Agenda"})
        primera = (grilla[0]["dia"], grilla[0]["hora"])
        self.assertEqual(self._approve(self._request("ergo-hosp", "30999507"), *primera,
                                       medico="Nico Sin Agenda")[0], 200)
        despues = _CLIENT.get(base + "&respaldo=servicio", headers=_admin_headers()).json().get("libres")
        self.assertNotIn(primera, {(t["dia"], t["hora"]) for t in despues})


if __name__ == "__main__":
    unittest.main()
