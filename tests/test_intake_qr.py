"""test_intake_qr.py — ingreso por QR de puerta (rediseñado 2026-08-01:
login primero, DNI de respaldo — ver docs/architecture/entrevista-previa.md):
GET /e/<door_token>, POST /api/intake/qr/<door_token>/start
(modules/intake/interview_patient.py) y el cartel imprimible
GET/POST /api/admin/intake/door/<medico_id>[/regenerate]
(modules/intake/intake_admin.py).

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest \
         discover -s tests -p "test_intake_qr.py"

Sigue el patrón de test_intake_http.py (router FastAPI real, rutas del
paciente) + test_intake_consult_admin.py (cookie firmada con
admin_auth._mint_token para las rutas admin). Los médicos de prueba se
insertan de VERDAD en la tabla `catalog` (db_catalog.catalog_upsert) en vez de
mockear catalog.doctors() — así `intake_admin._write_door_token` (que
escribe sobre ese mismo overlay) se ejercita de punta a punta, no contra un
mock que nunca refleja la escritura.
"""

import os
import sys
import json
import unittest
from datetime import datetime
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1", TURNOS_ADMIN_USER="admin")
_env.process_defaults(TURNOS_ADMIN_PASS="test-pass-123")
_env.process_set(TURNOS_ADMIN_TOTP_SECRET="", TURNOS_TURNSTILE_SECRET="")

from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_catalog  # noqa: E402
from app.patient_web import catalog           # noqa: E402
from app.auth import admin_auth       # noqa: E402
from app.core import config  # noqa: E402
from app.auth import turnstile          # noqa: E402
from app.core import tz as _tz  # noqa: E402
from app.modules.turnos import booking_requests   # noqa: E402
from app.modules.intake import (  # noqa: E402
    intake_admin, intake_admin_router, interview_patient,
    interview_patient_router,
)

turnstile.ENV_PATH = ""
config.ENV_PATH = ""

import _dbtest  # noqa: E402

try:
    import segno as _segno_probe  # noqa: F401
    HAVE_SEGNO = True
except ImportError:
    HAVE_SEGNO = False

_db = _dbtest.PerModuleDb("sana_turnos_test_intakeqr", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL = _db.test_url


def tearDownModule():
    _db.tear_down()


def _reset_tables():
    if not HAVE_DB:
        return
    import psycopg
    with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
        with c.cursor() as cur:
            cur.execute("TRUNCATE operators RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE interviews RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE consultations RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE chat_messages")
            cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
            cur.execute("TRUNCATE catalog")
    catalog.invalidate()


_env.process_set(ENABLED_MODULES="turnos,intake")
_APP = FastAPI()
_APP.include_router(interview_patient_router.router)
_APP.include_router(intake_admin_router.router)
_CLIENT = TestClient(_APP)


def _cookie(usuario, rol):
    return {"Cookie": "st_admin=" + admin_auth._mint_token(usuario, rol)}


def _admin_headers():
    return _cookie(admin_auth._admin_user(), "admin")


_MED1 = {"id": "qr-med-1", "nombre": "María José Fernández",
        "especialidad": "Cardiología", "centro": "centro-a",
        "entrevista_habilitada": True}
_MED2 = {"id": "qr-med-2", "nombre": "Carlos Gómez",
        "especialidad": "Pediatría", "centro": "centro-a",
        "entrevista_habilitada": True}


def _seed_doctor(doc):
    db_catalog.catalog_upsert("medicos", doc["id"], dict(doc), borrado=False)
    catalog.invalidate()
    return doc


def _hoy_mediodia():
    """Hoy a las 12:00 en el huso de la instalación — cae dentro del mismo
    día ART sin riesgo de cruzar medianoche por la hora real del test."""
    ahora = datetime.now(_tz.tzinfo())
    return ahora.replace(hour=12, minute=0, second=0, microsecond=0)


class BaseQrTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_tables()
        admin_auth._ATTEMPTS.clear()
        interview_patient._HITS.clear()
        _env.set_env(self, INTAKE_PER_HOUR=None)
        self._orig_get_user = booking_requests._get_user
        booking_requests._get_user = lambda h: None  # sin sesión salvo que el test la pise

    def tearDown(self):
        catalog.invalidate()
        booking_requests._get_user = self._orig_get_user

    # ---- helpers --------------------------------------------------------

    def _seed_solicitud_aprobada(self, dni, nombre, apellido, medico_id, turno_dt=None):
        sid = db_booking.create_booking_request(dni, nombre, apellido, "1980-01-01",
                                        "wa:11-5555", "serv-cardio", "centro-a")
        # sobreturno=True: varios tests siembran pacientes DISTINTOS con el
        # default turno_dt=_hoy_mediodia() para el MISMO médico (el matcheo QR
        # es lo que se prueba, no la exclusividad del horario) — desde
        # booking_requests_doctor_slot_uniq (2026-08-05) esa combinación exige
        # la misma exención que un sobreturno real.
        db_booking.approve(sid, turno_dt or _hoy_mediodia(), medico_asignado_id=medico_id,
                  resuelta_por="admin", sobreturno=True)
        return sid

    def _con_sesion(self, dni, nombre=None, apellido=None):
        """Simula un visitante YA logueado en el sitio (site_auth de
        cuenta) — mismo patrón de monkeypatch que
        test_intake_http.py:TestPaginaEntrevista."""
        booking_requests._get_user = lambda h: {
            "dni": dni, "nombre": nombre, "apellido": apellido}

    def _get_puerta(self, token):
        return _CLIENT.get(f"/e/{token}"), True

    def _post_start(self, token, body, headers=None):
        return _CLIENT.post(
            f"/api/intake/qr/{token}/start", json=body,
            headers=headers or {}), True

    def _get_poster(self, medico_id, headers):
        return _CLIENT.get(
            f"/api/admin/intake/door/{medico_id}", headers=headers), True

    def _post_regenerate(self, medico_id, headers):
        return _CLIENT.post(
            f"/api/admin/intake/door/{medico_id}/regenerate",
            headers=headers), True


class TestRuteoAjeno(BaseQrTestCase):
    def test_get_puerta_desconocida_no_es_otra_ruta(self):
        self.assertEqual(_CLIENT.get("/otra-cosa").status_code, 404)

    def test_post_ajeno_no_maneja(self):
        response = _CLIENT.post("/api/intake/qr/tok/otra-cosa")
        self.assertEqual(response.status_code, 404)

    def test_intake_admin_ruta_ajena_no_maneja(self):
        self.assertEqual(_CLIENT.get("/api/admin/otra-cosa").status_code, 404)


# ---------------------------------------------------------------- GET /e/<token>

class TestPaginaQr(BaseQrTestCase):
    def test_token_desconocido_404(self):
        h, manejado = self._get_puerta("no-existe")
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 404)

    def test_sin_sesion_pinta_login_primero_y_dni_de_respaldo(self):
        """Visitante anónimo: NUNCA ve un formulario de nombre+apellido —
        la acción primaria es "Ingresá a tu cuenta" (vuelve acá con
        ?next=/e/<token>) y el DNI queda como respaldo debajo."""
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        h, manejado = self._get_puerta(token)
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 200)
        body = h.text
        self.assertIn(f'data-qr-start="{token}"', body)
        self.assertIn(f'data-account-next="/e/{token}"', body)
        self.assertNotIn("data-token=", body)

    def test_medico_deshabilitado_404(self):
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = False
        _seed_doctor(doc)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        h, manejado = self._get_puerta(token)
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 404)

    def test_con_sesion_saltea_identidad_y_matchea_por_dni(self):
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        sid = self._seed_solicitud_aprobada("30111230", "Rosa", "Díaz", _MED1["id"])
        self._con_sesion("30111230", "Rosa", "Díaz")

        h, manejado = self._get_puerta(token)
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 200)
        body = h.text
        self.assertIn('data-token="', body)
        self.assertNotIn("data-qr-start=", body)

        # la fila real que se creó matchea el turno de hoy
        import re
        m = re.search(r'data-token="([^"]+)"', body)
        interview = db_clinical.interview_by_patient_token(m.group(1))
        self.assertTrue(interview["matched"])
        self.assertEqual(interview["request_id"], sid)
        self.assertEqual(interview["dni"], "30111230")
        self.assertEqual(interview["entered_name"], "Rosa")
        self.assertEqual(interview["entered_surname"], "Díaz")

    def test_con_sesion_sin_turno_hoy_igual_entra_al_chat(self):
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        self._con_sesion("30111231", "Nadie", "Conturno")

        h, manejado = self._get_puerta(token)
        self.assertEqual(h.status_code, 200)
        import re
        m = re.search(r'data-token="([^"]+)"', h.text)
        interview = db_clinical.interview_by_patient_token(m.group(1))
        self.assertFalse(interview["matched"])
        self.assertIsNone(interview["request_id"])
        self.assertEqual(interview["dni"], "30111231")


# ---------------------------------------------------------------- POST start

class TestQrStart(BaseQrTestCase):
    """POST /api/intake/qr/<door_token>/start — SOLO el respaldo del
    visitante anónimo (sin sesión); el body es {dni}, no {nombre,apellido}
    (retirado 2026-08-01: "Name fields are GONE from this flow")."""

    def test_token_desconocido_404(self):
        h, manejado = self._post_start("no-existe", {"dni": "27888999"})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 404)

    def test_dni_invalido_400(self):
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        h, _ = self._post_start(token, {"dni": "12"})
        self.assertEqual(h.status_code, 400)
        self.assertEqual(h.json()["error"], "dni_invalido")

    def test_dni_vacio_400(self):
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        h, _ = self._post_start(token, {"dni": "  "})
        self.assertEqual(h.status_code, 400)

    def test_medico_deshabilitado_rechaza_el_start(self):
        doc = dict(_MED1)
        doc["entrevista_habilitada"] = False
        _seed_doctor(doc)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        h, _ = self._post_start(token, {"dni": "30111222"})
        self.assertEqual(h.status_code, 404)

    def test_match_exacto_matchea_y_copia_request_id(self):
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        sid = self._seed_solicitud_aprobada("30111222", "Ana", "López", _MED1["id"])

        h, manejado = self._post_start(token, {"dni": "30111222"})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 200)
        self.assertIn("patient_token", h.json())

        interview = db_clinical.interview_by_patient_token(h.json()["patient_token"])
        self.assertIsNotNone(interview)
        self.assertTrue(interview["matched"])
        self.assertEqual(interview["request_id"], sid)
        self.assertEqual(interview["dni"], "30111222")
        # visitante anónimo: nunca sabemos su nombre, sólo el DNI que tipeó
        self.assertIsNone(interview["entered_name"])
        self.assertIsNone(interview["entered_surname"])

    def test_match_tolera_puntos_y_espacios_en_el_dni(self):
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        self._seed_solicitud_aprobada("30111223", "María José", "Núñez", _MED1["id"])

        h, _ = self._post_start(token, {"dni": "30.111.223"})
        self.assertEqual(h.status_code, 200)
        interview = db_clinical.interview_by_patient_token(h.json()["patient_token"])
        self.assertTrue(interview["matched"])

    def test_cero_matches_crea_entrevista_sin_turno(self):
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])

        h, _ = self._post_start(token, {"dni": "40999888"})
        self.assertEqual(h.status_code, 200)
        interview = db_clinical.interview_by_patient_token(h.json()["patient_token"])
        self.assertFalse(interview["matched"])
        self.assertIsNone(interview["request_id"])
        self.assertEqual(interview["entry_mode"], "qr")
        self.assertEqual(interview["dni"], "40999888")

    def test_dos_turnos_mismo_dni_no_matchea(self):
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        self._seed_solicitud_aprobada("30111224", "Juan", "Pérez", _MED1["id"])
        self._seed_solicitud_aprobada("30111224", "Juan", "Pérez", _MED1["id"])

        h, _ = self._post_start(token, {"dni": "30111224"})
        self.assertEqual(h.status_code, 200)
        interview = db_clinical.interview_by_patient_token(h.json()["patient_token"])
        self.assertFalse(interview["matched"])
        self.assertIsNone(interview["request_id"])

    def test_solo_matchea_turnos_del_medico_de_ese_qr(self):
        _seed_doctor(_MED1)
        _seed_doctor(_MED2)
        token1, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        # el turno de Ana es de OTRO médico (med-2) — el QR de med-1 no debe matchearlo
        self._seed_solicitud_aprobada("30111226", "Ana", "López", _MED2["id"])

        h, _ = self._post_start(token1, {"dni": "30111226"})
        self.assertEqual(h.status_code, 200)
        interview = db_clinical.interview_by_patient_token(h.json()["patient_token"])
        self.assertFalse(interview["matched"])

    def test_respuesta_nunca_expone_si_matcheo(self):
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        sid = self._seed_solicitud_aprobada("30111227", "Rosa", "Díaz", _MED1["id"])

        h1, _ = self._post_start(token, {"dni": "30111227"})
        h2, _ = self._post_start(token, {"dni": "40999777"})
        self.assertEqual(set(h1.json().keys()), {"patient_token"})
        self.assertEqual(set(h2.json().keys()), {"patient_token"})
        self.assertNotIn("matched", h1.json())
        self.assertNotIn("request_id", h1.json())
        # sanity: uno matcheó y el otro no, pero la FORMA de la respuesta es idéntica
        iv1 = db_clinical.interview_by_patient_token(h1.json()["patient_token"])
        iv2 = db_clinical.interview_by_patient_token(h2.json()["patient_token"])
        self.assertTrue(iv1["matched"])
        self.assertFalse(iv2["matched"])
        _ = sid

    def test_rate_limit_429(self):
        _env.set_env(self, INTAKE_PER_HOUR="1")
        _seed_doctor(_MED1)
        token, _ = intake_admin.get_or_create_door_token(_MED1["id"])
        headers = {"X-Forwarded-For": "9.9.9.9"}
        h1, _ = self._post_start(token, {"dni": "40111001"}, headers=headers)
        self.assertEqual(h1.status_code, 200)
        h2, _ = self._post_start(token, {"dni": "40111002"}, headers=headers)
        self.assertEqual(h2.status_code, 429)


# ---------------------------------------------------------------- cartel (poster)

class TestCartelPuerta(BaseQrTestCase):
    def test_sin_sesion_401(self):
        _seed_doctor(_MED1)
        h, manejado = self._get_poster(_MED1["id"], headers={})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 401)

    def test_admin_ve_cualquier_cartel_y_contiene_la_url_de_e(self):
        _seed_doctor(_MED1)
        h, _ = self._get_poster(_MED1["id"], headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        token = catalog.doctors()
        token = next(m for m in token if m["id"] == _MED1["id"])["entrevista_qr_token"]
        self.assertIn(f"/e/{token}", h.text)

    def test_medico_ve_su_propio_cartel(self):
        _seed_doctor(_MED1)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])
        h, _ = self._get_poster(_MED1["id"], headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 200)

    def test_medico_404_en_cartel_de_otro_medico(self):
        _seed_doctor(_MED1)
        _seed_doctor(_MED2)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])
        h, _ = self._get_poster(_MED2["id"], headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 404)

    def test_medico_inexistente_404(self):
        h, _ = self._get_poster("no-existe", headers=_admin_headers())
        self.assertEqual(h.status_code, 404)

    def test_token_se_crea_una_sola_vez_idempotente(self):
        _seed_doctor(_MED1)
        h1, _ = self._get_poster(_MED1["id"], headers=_admin_headers())
        h2, _ = self._get_poster(_MED1["id"], headers=_admin_headers())
        token1 = next(m for m in catalog.doctors() if m["id"] == _MED1["id"])["entrevista_qr_token"]
        self.assertIn(f"/e/{token1}", h1.text)
        self.assertIn(f"/e/{token1}", h2.text)

    def test_cartel_funciona_sin_segno_cae_a_la_url(self):
        _seed_doctor(_MED1)
        with mock.patch.object(intake_admin, "_qr_svg", return_value=None):
            h, _ = self._get_poster(_MED1["id"], headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        body = h.text
        token = next(m for m in catalog.doctors() if m["id"] == _MED1["id"])["entrevista_qr_token"]
        self.assertIn(f"/e/{token}", body)
        self.assertNotIn("<svg", body)
        self.assertIn("segno", body)

    @unittest.skipUnless(HAVE_SEGNO, "segno no está instalado")
    def test_cartel_incluye_svg_cuando_segno_esta_instalado(self):
        _seed_doctor(_MED1)
        h, _ = self._get_poster(_MED1["id"], headers=_admin_headers())
        self.assertEqual(h.status_code, 200)
        self.assertIn("<svg", h.text)


class TestRegenerarToken(BaseQrTestCase):
    def test_sin_sesion_401(self):
        _seed_doctor(_MED1)
        h, manejado = self._post_regenerate(_MED1["id"], headers={})
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 401)

    def test_medico_404_en_medico_ajeno(self):
        _seed_doctor(_MED1)
        _seed_doctor(_MED2)
        db_operators.operator_create("dr-fernandez", "clave1234", "medico", medico_id=_MED1["id"])
        h, _ = self._post_regenerate(_MED2["id"], headers=_cookie("dr-fernandez", "medico"))
        self.assertEqual(h.status_code, 404)

    def test_regenerar_cambia_el_token_y_el_viejo_deja_de_resolver(self):
        _seed_doctor(_MED1)
        token_viejo, _ = intake_admin.get_or_create_door_token(_MED1["id"])

        h, manejado = self._post_regenerate(_MED1["id"], headers=_admin_headers())
        self.assertTrue(manejado)
        self.assertEqual(h.status_code, 200)
        self.assertTrue(h.json()["ok"])

        token_nuevo = next(m for m in catalog.doctors()
                           if m["id"] == _MED1["id"])["entrevista_qr_token"]
        self.assertNotEqual(token_viejo, token_nuevo)

        # el token viejo ya no resuelve nada
        self.assertIsNone(intake_admin.doctor_by_door_token(token_viejo))
        h_viejo, _ = self._get_puerta(token_viejo)
        self.assertEqual(h_viejo.status_code, 404)

        # el token nuevo sí
        h_nuevo, _ = self._get_puerta(token_nuevo)
        self.assertEqual(h_nuevo.status_code, 200)


if __name__ == "__main__":
    unittest.main()
