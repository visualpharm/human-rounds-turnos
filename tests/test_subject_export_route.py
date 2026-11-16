"""«Exportar datos del paciente» — GET /api/admin/patients/{dni}/data-export
(Ley 25.326 art. 14, P2). Sólo el admin; deja su fila `exportacion` en el
registro de accesos ANTES de armar nada; una ficha que no existe es 404 sin
fila; sin la fila no hay copia (503).

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning \
         -m unittest discover -s tests -p "test_subject_export_route.py"
"""
import io
import os
import sys
import unittest
import zipfile
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="")
_env.process_set(TURNOS_TURNSTILE_SECRET="")
_env.process_set(ENABLED_MODULES="turnos,intake")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import admin_auth  # noqa: E402
from app.auth import turnstile  # noqa: E402
from app.admin_panel import admin_patients_router  # noqa: E402
from app.core import access_log, config  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import deps as core_deps  # noqa: E402
import _dbtest  # noqa: E402
import _subject_fixture  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""

_db = _dbtest.PerModuleDb("sana_turnos_test_subject_route", seed=lambda: db_operators.operators_list())
_APP = FastAPI()
_APP.add_exception_handler(core_deps.AdminGateError, core_deps.render_admin_gate_error)
_APP.include_router(admin_patients_router.router)
_CLIENT = TestClient(_APP)
P = "20111222"


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason


def tearDownModule():
    _db.tear_down()


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin():
    return _cookie(admin_auth._admin_user(), "admin")


def _acciones(dni):
    return [(r["actor"], r["action"], r["allowed"]) for r in reversed(access_log.history(dni))]


class TestRoute(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        self.fx = _subject_fixture.Fixture(_db.test_url, self)
        self.fx.reset()
        self.fx.execute("TRUNCATE operators, operator_access RESTART IDENTITY CASCADE")
        self.fx.execute("TRUNCATE clinical_access_log RESTART IDENTITY")
        access_log._seen.clear()
        admin_auth._ATTEMPTS.clear()
        db_operators.operator_create("mesa", "clave1234", "admisionista")
        db_operators.operator_create("dra", "clave1234", "medico", medico_id="med-1")
        self.p = self.fx.seed_person(P, "Pia", with_files=False)

    def test_the_admin_gets_a_zip_and_leaves_one_access_row(self):
        r = _CLIENT.get(f"/api/admin/patients/{P}/data-export", headers=_admin())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/zip")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertFalse(P in r.headers["content-disposition"])
        self.assertEqual(int(r.headers["content-length"]), len(r.content))  # streamed, sized
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertIn("manifest.json", z.namelist())
        self.assertIn("data/users.json", z.namelist())
        self.assertEqual(_acciones(P), [(admin_auth._admin_user(), "exportacion", True)])

    def test_nobody_but_the_admin_can_ask(self):
        for usuario, rol in (("mesa", "admisionista"), ("dra", "medico")):
            r = _CLIENT.get(f"/api/admin/patients/{P}/data-export", headers=_cookie(usuario, rol))
            self.assertEqual(r.status_code, 403, usuario)
        self.assertEqual(_CLIENT.get(f"/api/admin/patients/{P}/data-export").status_code, 401)
        self.assertEqual(_acciones(P), [])

    def test_a_patient_who_does_not_exist_is_a_404_without_a_row(self):
        r = _CLIENT.get("/api/admin/patients/39999999/data-export", headers=_admin())
        self.assertEqual(r.status_code, 404)
        self.assertEqual(_acciones("39999999"), [])
        self.assertEqual(_CLIENT.get("/api/admin/patients/abc/data-export", headers=_admin()).status_code, 404)

    def test_when_the_access_row_cannot_be_written_nothing_is_built(self):
        with mock.patch.object(db_clinical, "insert_clinical_access", side_effect=db.DbUnavailable("down")), \
                mock.patch("app.core.subject_export.export_to_tempfile") as build:
            r = _CLIENT.get(f"/api/admin/patients/{P}/data-export", headers=_admin())
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json(), {"error": "registro_de_accesos_no_disponible"})
        build.assert_not_called()

    def test_the_phone_of_the_request_widens_the_search(self):
        self.fx.execute("UPDATE users SET contact = NULL WHERE dni = %s", (P,))
        r = _CLIENT.get(f"/api/admin/patients/{P}/data-export?phone=2255123456", headers=_admin())
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertIn("wa Pia", z.read("data/chat_messages.whatsapp.json").decode())
        r = _CLIENT.get(f"/api/admin/patients/{P}/data-export", headers=_admin())
        z = zipfile.ZipFile(io.BytesIO(r.content))
        self.assertEqual(z.read("data/chat_messages.whatsapp.json").decode().strip(), "[]")


if __name__ == "__main__":
    unittest.main()
