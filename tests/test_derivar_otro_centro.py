"""test_derivar_otro_centro.py — «Derivar» a un servicio de OTRO centro (2026-10-29).

Video del 29/10 de la mesa de un hospital: una solicitud de ginecología se
deriva a «Medicina general – Salud de la mujer», que atiende el CAPS, y la
tarjeta se quedaba con el Hospital y sin horarios.

Lo que este archivo fija: una mesa acotada a un centro pide los horarios del
servicio derivado y los recibe aunque ese servicio sea de otro centro
(`derivacion=1`): es la misma agenda que ve cualquiera en la web, y la
solicitud sale de su alcance al aprobarse. Sin `derivacion`, la mesa acotada
sigue sin ver lo ajeno. Que al aprobar la solicitud pase al centro del servicio
nuevo lo fija test_booking_requests.py.

Correr: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_derivar_otro_centro.py
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

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", MAIL_ENABLED="0",
                      TURNOS_ADMIN_PASS="test-pass-123")

from app.core import db  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from app.admin_panel import admin_appointments  # noqa: E402
from app.core import tz  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_derivar_centro", seed=lambda: db_booking.get_booking_request(-1))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


_FRANJA = {"dias": [1, 2, 3, 4, 5], "desde": "09:00", "hasta": "12:00", "cada_min": 20}
_SERVICIOS = {"servicios": [
    {"id": "gineco-hosp", "nombre": "Ginecología", "centro": "hospital",
     "cluster": "ginecologia", "categoria": "Consultorios", "modo": "solicitud"},
    {"id": "mujer-caps", "nombre": "Medicina general - Salud de la mujer", "centro": "caps",
     "cluster": "salud-de-la-mujer", "categoria": "Consultorios", "modo": "solicitud",
     "agenda": dict(_FRANJA)},
]}
_CENTROS = {"centros": [{"slug": "hospital", "name": "Hospital", "address": "Av. 1"},
                        {"slug": "caps", "name": "CAPS", "address": "Av. 2"}]}
_MESA_HOSPITAL = {"usuario": "mesa-hospital", "rol": "admisionista"}


def _un_dia_habil():
    d = datetime.now(tz.tzinfo()).date() + timedelta(days=1)
    while d.isoweekday() > 5:
        d += timedelta(days=1)
    return d


class DerivarOtroCentroTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        with db._connect() as c, c.cursor() as cur:
            cur.execute("TRUNCATE booking_requests, users, catalog, slot_holds RESTART IDENTITY CASCADE")
        self._tmp = tempfile.mkdtemp(prefix="derivar-centro-test-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir)
        for name, doc in (("servicios.json", _SERVICIOS), ("centros-salud.json", _CENTROS),
                          ("medicos.json", {"medicos": []})):
            with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
                json.dump(doc, f)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
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

    def _slots(self, servicio_id, identity, **extra):
        qs = {"servicio_id": [servicio_id]}
        qs.update({k: [v] for k, v in extra.items()})
        with mock.patch.object(admin_auth, "centros_permitidos", return_value={"hospital"}):
            return admin_appointments.slots_data(identity, qs)

    # ---- los horarios del servicio derivado ----------------------------------

    def test_la_mesa_acotada_ve_los_horarios_del_servicio_derivado_de_otro_centro(self):
        r = self._slots("mujer-caps", _MESA_HOSPITAL, derivacion="1")
        self.assertTrue(r["libres"], r)
        primero = datetime.fromisoformat(r["libres"][0]["dia"]).date()
        self.assertLessEqual(primero, _un_dia_habil())  # el más próximo primero: hoy o mañana

    def test_el_alcance_por_centro_sigue_cerrando_sin_derivacion(self):
        # fuera de «Derivar», la mesa acotada no ve la agenda de otro centro
        self.assertEqual(self._slots("mujer-caps", _MESA_HOSPITAL), {"libres": [], "sobreturnos": []})

    def test_la_mesa_acotada_ve_lo_de_su_propio_centro_con_o_sin_derivacion(self):
        # el servicio del Hospital no ofrece turnos (sin agenda): igual en ambos casos
        self.assertEqual(self._slots("gineco-hosp", _MESA_HOSPITAL, derivacion="1"),
                         self._slots("gineco-hosp", _MESA_HOSPITAL))


if __name__ == "__main__":
    unittest.main()
