"""test_admin_cola_xss.py — regresión del XSS almacenado en /admin/cola.

Auditoría de seguridad 2026-09-27, hallazgo 1 (HIGH): el nombre de un
dependiente (`POST /api/account/dependents`, sólo `.strip()`) viajaba
verbatím por la solicitud de turno hasta la fila de la cola del día
(doctor_appointments._appointment_public) y el render inicial del servidor
(`templates/admin/partials/day_queue_initial.html`) lo emitía SIN escapar —
el entorno Jinja compartido tenía `autoescape=False` y el formateador
`person_name` normaliza mayúsculas pero no escapa HTML. Un `<img src=x
onerror=…>` como nombre ejecutaba script en la sesión del staff.

El cierre es de MECANISMO, no de mancha puntual: `core/jinja_env.py` now
corre con autoescape y los valores pre-escapados/confiables cruzan como
`Markup`. Dos pruebas:

1. Sin base (rápida, corre siempre): el partial de la cola inicial, rendido
   por el entorno real, escapa un nombre hostil — pin del mecanismo.
2. Con base (TEST_DATABASE_URL): la cadena COMPLETA del hallazgo — alta de
   dependiente con nombre hostil (accounts._add_dependent_core, la misma
   función que sirve el POST), booking para ese dependiente
   (booking_create.create_request, que copia el nombre almacenado),
   aprobación del staff y GET /admin/cola con sesión admin real: el HTML
   trae el payload entity-escapado y ningún `<img` crudo en ninguna parte
   (tampoco dentro del islote JSON, que viaja |tojson).

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_admin_cola_xss.py"
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.auth import admin_auth, turnstile  # noqa: E402
from app.core import config, i18n, jinja_env, tz  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import format as display_format  # noqa: E402
from app.admin_panel import admin_cola_screen  # noqa: E402
from app.cuentas import accounts  # noqa: E402
from app.modules.turnos import booking_create, doctor_appointments  # noqa: E402
from app.patient_web import catalog  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app import main as app_module  # noqa: E402

_CLIENT = TestClient(app_module.app)
turnstile.ENV_PATH = ""   # no leer el .env real (mismo truco que los otros tests)
config.ENV_PATH = ""

_ART = tz.tzinfo()

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_colaxss", seed=lambda: db_operators.operators_list())


def setUpModule():
    _db.set_up()


def tearDownModule():
    _db.tear_down()

# El payload exacto del hallazgo: nombre-válido-para-la-API, markup vivo
# para el navegador.
PAYLOAD = '<img src=x onerror=alert(1)>'
ESCAPADO = '&lt;img src=x onerror=alert(1)&gt;'


# ---- 1. mecanismo: el partial inicial escapa, sin base -----------------------

class TestColaInitialRenderEscapes(unittest.TestCase):
    """El render del servidor (pre-JavaScript) de la tabla de la cola no
    puede emitir markup vivo venga de donde venga el nombre."""

    def _render(self):
        _lang, token = i18n.bind_lang("es")
        try:
            return jinja_env.render(
                "admin/partials/day_queue_initial.html",
                queue_data={"grupos": [{
                    "medico": "Dra. María José Fernández",
                    "turnos": [{
                        "hora": "10:00",
                        "paciente": PAYLOAD + " XSS",
                        "servicio": "Cardiología",
                        "asistencia": None,
                    }],
                }], "total": 1},
                # El mismo formateador que admin_cola_screen pasa al template:
                # normaliza mayúsculas, NO escapa — el escape es del entorno.
                queue_display_name=display_format.person_name,
                queue_name_age=admin_cola_screen.name_age,
            )
        finally:
            i18n.unbind(token)

    def test_el_nombre_hostil_sale_entity_escapado(self):
        html = self._render()
        self.assertIn(ESCAPADO, html)
        self.assertNotIn(PAYLOAD, html)

    def test_los_datos_quedan_legibles_tras_el_escape(self):
        html = self._render()
        # texto visible post-unescape: el apellido y el servicio siguen ahí
        self.assertIn("XSS", html)
        self.assertIn("Cardiología", html)


# ---- 2. cadena completa del hallazgo, con base --------------------------------

_CENTROS = {"centros": [{"slug": "centro-a", "name": "Centro A", "address": "Calle 1"}]}
_SERVICIOS = {"servicios": [
    {"id": "serv-cardio", "nombre": "Cardiología", "centro": "centro-a",
     "modo": "solicitud", "cluster": "cardiologia"},
]}
_MEDICOS = {"medicos": [
    {"id": "med-1", "nombre": "María José Fernández", "especialidad": "Cardiología",
     "centro": "centro-a", "cluster": "cardiologia"},
]}

_TITULAR = {"dni": "27888998", "nombre": "Ana", "apellido": "López",
            "nacimiento": "1979-06-01", "sexo": "F", "contacto": "wa:11-5555"}


class TestDependentNameXssEndToEnd(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        if not _db.have_db:
            self.skipTest(_db.skip_reason or "sin base de datos")
        self._reset_tables()
        admin_auth._ATTEMPTS.clear()
        self._tmp = tempfile.mkdtemp(prefix="cola-xss-test-")
        webdir = os.path.join(self._tmp, "webdata")
        os.makedirs(webdir, exist_ok=True)
        for name, data in (("centros-salud.json", _CENTROS), ("servicios.json", _SERVICIOS),
                           ("medicos.json", _MEDICOS)):
            with open(os.path.join(webdir, name), "w", encoding="utf-8") as f:
                json.dump(data, f)
        # Registered before set_env: cleanups run last-in first-out, so the caches are dropped after the environment is back.
        self.addCleanup(catalog._JSON_CACHE.clear)
        self.addCleanup(catalog.invalidate)
        _env.set_env(self, TURNOS_WEBDATA_DIR=webdir)
        catalog._JSON_CACHE.clear()
        catalog.invalidate()
        # La cola diaria del día de HOY es la que /admin/cola sirve inicial.
        hoy = datetime.now(_ART).date()
        self._turno_dt = datetime(hoy.year, hoy.month, hoy.day, 10, 0, tzinfo=_ART)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        self._reset_tables()

    def _reset_tables(self):
        if not _db.have_db:
            return
        import psycopg
        with psycopg.connect(_db.test_url, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")
                cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
                cur.execute("TRUNCATE users RESTART IDENTITY CASCADE")
                cur.execute("TRUNCATE dependents RESTART IDENTITY")
                cur.execute("TRUNCATE catalog")

    def _admin_headers(self):
        return {"Cookie": "st_admin=" + admin_auth._mint_token(admin_auth._admin_user(), "admin")}

    def test_el_nombre_del_dependiente_llega_escapado_a_la_cola(self):
        # 1. El paciente de la cuenta alta el dependiente hostil — la MISMA
        #    validación que sirve el POST /api/account/dependents (sólo
        #    .strip(): el payload es un nombre "válido" y se guarda tal cual;
        #    por eso el render tiene que escapar, no la entrada a rechazar).
        _dbtest.ensure_user(_TITULAR["dni"])
        resultado = accounts._add_dependent_core(dict(_TITULAR), {
            "dni": "50333444", "nombre": PAYLOAD, "apellido": "XSS",
            "nacimiento": "2015-05-05", "sexo": "M", "vinculo": "hijo",
            "declaracion_jurada": True,
        })
        self.assertEqual(resultado.status, 200, resultado.json_body)
        self.assertTrue(resultado.json_body.get("ok"))

        # 2. Pide el turno PARA el dependiente: create_request copia el nombre
        #    almacenado verbatím a la fila (booking_requests.py:1184) —
        #    exactamente el salto del hallazgo.
        estado, payload = booking_create.create_request(
            dict(_TITULAR), {"servicio_id": "serv-cardio", "centro_slug": "centro-a",
                             "paciente_dni": "50333444"})
        self.assertEqual(estado, 200, payload)
        sid = payload["id"]

        # 3. El staff aprueba para hoy — el momento en que la fila entra a la
        #    cola del día que la pantalla sirve.
        db_booking.approve(sid, self._turno_dt, medico_asignado_id="med-1", resuelta_por="admin")

        # 4. /admin/cola con sesión admin real: el HTML inicial del servidor.
        r = _CLIENT.get("/admin/cola", headers=self._admin_headers(),
                        follow_redirects=False)
        self.assertEqual(r.status_code, 200, r.text[:500])
        html = r.text
        self.assertIn(ESCAPADO, html)          # el nombre, entity-escapado
        self.assertIn("XSS", html)             # el apellido llegó igual
        self.assertNotIn(PAYLOAD, html)        # ni la tabla NI el islote JSON
        self.assertNotIn("<img src=x", html)   # ningún <img> crudo en la página


if __name__ == "__main__":
    unittest.main()
