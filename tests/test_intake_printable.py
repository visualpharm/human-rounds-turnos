"""test_intake_printable.py — GET /r/<receta_token> y /o/<orden_token>
(modules/intake/printable.py, fase 5).

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
         discover -s tests -p "test_intake_printable.py"

No pega a la IA ni al catálogo — printable.py sólo renderiza receta_json/
orden_json YA guardados en `consultations`. Misma base de test dedicada que
test_intake_consult_admin.py (router FastAPI real, _dbtest.PerModuleDb)."""

import os
import sys
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db_operators  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import config  # noqa: E402
from app.auth import turnstile          # noqa: E402
from app.modules.intake import printable_router  # noqa: E402

turnstile.ENV_PATH = ""
config.ENV_PATH = ""

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_intakeprintable", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


def _reset_tables():
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE interviews RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE consultations RESTART IDENTITY CASCADE")


_env.process_set(ENABLED_MODULES="turnos,intake")
_APP = FastAPI()
_APP.include_router(printable_router.router)
_CLIENT = TestClient(_APP)


_RECETA_JSON = {
    "medico": {"nombre": "Dra. Fernández", "matricula": "92933", "especialidad": "Cardiología"},
    "paciente": {"nombre": "Gómez, Ana", "dni": "30111222"},
    "fecha": "31/07/2026",
    "espacio_superior_cm": 2,
    "titulo": "Hospital de Pinamar",
    "lineas": ["Paracetamol — 500 mg — cada 8 h — 5 días"],
    "notas": "Control en 15 días.",
    "pie": "Documento generado por Hospital de Pinamar — validalo con tu médico.",
}

_ORDEN_JSON = {
    "medico": {"nombre": "Dra. Fernández", "matricula": "92933", "especialidad": "Cardiología"},
    "paciente": {"nombre": "Gómez, Ana", "dni": "30111222"},
    "fecha": "31/07/2026",
    "espacio_superior_cm": 0,
    "titulo": "Hospital de Pinamar",
    "estudios": ["Hemograma completo"],
    "diagnosticos": ["Hipertensión arterial esencial (CIE-10 I10)"],
    "notas": "",
    "pie": "Documento generado por Hospital de Pinamar — validalo con tu médico.",
}


class PrintableTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()

    def _crear_consulta(self, receta_json=None, orden_json=None):
        interview = db_clinical.interview_create(doctor_id="med-1", entry_mode="post_booking", matched=True)
        return db_clinical.consultation_create(
            interview_id=interview["id"], doctor_id="med-1",
            diagnoses=[], exams=[], meds=[], notes="",
            receta_json=receta_json if receta_json is not None else _RECETA_JSON,
            orden_json=orden_json if orden_json is not None else _ORDEN_JSON,
            receta_token="tok-receta-123", orden_token="tok-orden-456",
            created_by="admin",
        )

    def _get(self, path):
        return _CLIENT.get(path), True

    # ---- ruteo ------------------------------------------------------------

    def test_ruta_ajena_no_se_maneja(self):
        self.assertEqual(_CLIENT.get("/api/admin/otra-cosa").status_code, 404)

    def test_post_no_se_maneja(self):
        self.assertEqual(_CLIENT.post("/r/tok-receta-123").status_code, 405)

    # ---- receta -------------------------------------------------------------

    def test_receta_200_con_lineas_y_cuatro_copias(self):
        self._crear_consulta()
        h, manejado = self._get("/r/tok-receta-123")
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 200)
        body = h.text
        self.assertIn("Paracetamol — 500 mg — cada 8 h — 5 días", body)
        for copia in ("Paciente", "Farmacia", "Obra social", "Archivo"):
            self.assertIn(copia, body)
        # el header/paciente aparece UNA vez por copia -> 4 veces
        self.assertEqual(body.count("Gómez, Ana"), 4)

    def test_receta_token_de_orden_404_en_r(self):
        self._crear_consulta()
        h, manejado = self._get("/r/tok-orden-456")
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 404)

    def test_espacio_superior_cm_se_refleja_en_el_estilo(self):
        self._crear_consulta()
        h, _ = self._get("/r/tok-receta-123")
        self.assertIn("padding-top:calc(10mm + 2.0cm)", h.text)

    # ---- orden ----------------------------------------------------------------

    def test_orden_200_con_original_y_duplicado(self):
        self._crear_consulta()
        h, manejado = self._get("/o/tok-orden-456")
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 200)
        body = h.text
        self.assertIn("Original", body)
        self.assertIn("Duplicado", body)
        self.assertIn("Hemograma completo", body)
        self.assertIn("Hipertensión arterial esencial", body)

    def test_orden_token_de_receta_404_en_o(self):
        self._crear_consulta()
        h, manejado = self._get("/o/tok-receta-123")
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 404)

    # ---- tokens desconocidos ----------------------------------------------

    def test_token_desconocido_404(self):
        h, manejado = self._get("/r/no-existe-este-token")
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 404)
        h2, _ = self._get("/o/no-existe-este-token")
        self.assertEqual(h2.status_code, 404)

    # ---- escaping -----------------------------------------------------------

    def test_nombre_de_medicamento_malicioso_queda_escapado(self):
        payload = dict(_RECETA_JSON)
        payload["lineas"] = ["<script>alert(1)</script> — 500 mg — cada 8 h — 5 días"]
        self._crear_consulta(receta_json=payload)
        h, _ = self._get("/r/tok-receta-123")
        body = h.text
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)


if __name__ == "__main__":
    unittest.main()
