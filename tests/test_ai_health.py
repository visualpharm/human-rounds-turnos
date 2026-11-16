"""test_ai_health.py — AI health + graceful degradation (ai_health.py).

Run:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_ai_health.py"

La lógica de estado (clasificación de errores, transiciones, override, endpoint)
es 100% en memoria y corre siempre. El reproceso de lo acumulado necesita base.
"""

import os
import sys
import io
import json
import socket
import tempfile
import unittest
import urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.ai import ai_health  # noqa: E402
from app.ai import ai_health_router  # noqa: E402
from app.cuentas import ocr_dni     # noqa: E402
from app.cuentas import ocr_dni_router  # noqa: E402
from app.ai import ai_referral  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_operators  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

PNG_1x1 = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCA"
           "AAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _reset_state():
    ai_health._STATE.update({"disponible": True, "desde": None, "motivo": None})


def _no_forced_state(case):
    """The test starts with no TURNOS_AI_FORCE (the developer's own value included); put back after it."""
    _env.set_env(case, TURNOS_AI_FORCE=None)


# ---- clasificación de errores ----------------------------------------------

class ClasificacionTestCase(unittest.TestCase):
    def _http(self, code):
        err = urllib.error.HTTPError("http://x", code, "msg", {}, io.BytesIO(b""))
        self.addCleanup(err.close)
        return err

    def test_pago_cuota_auth_rate_5xx_son_caida(self):
        for code in (401, 402, 403, 408, 429, 500, 502, 503, 504):
            self.assertTrue(ai_health.is_service_error(self._http(code)), code)

    def test_redirect_refused_is_service_failure(self):
        for code in (301, 302, 307, 308):
            self.assertTrue(ai_health.is_service_error(self._http(code)), code)

    def test_400_404_413_no_son_caida(self):
        for code in (400, 404, 413):
            self.assertFalse(ai_health.is_service_error(self._http(code)), code)

    def test_timeout_y_red_son_caida(self):
        self.assertTrue(ai_health.is_service_error(socket.timeout()))
        self.assertTrue(ai_health.is_service_error(TimeoutError()))
        self.assertTrue(ai_health.is_service_error(ConnectionError()))
        self.assertTrue(ai_health.is_service_error(urllib.error.URLError("x")))

    def test_otras_excepciones_no_son_caida(self):
        self.assertFalse(ai_health.is_service_error(ValueError("x")))


# ---- transiciones de estado -------------------------------------------------

class EstadoTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        _no_forced_state(self)

    def tearDown(self):
        _reset_state()

    def test_arranca_disponible(self):
        self.assertTrue(ai_health.available())

    def test_error_de_servicio_baja_el_estado(self):
        with self_http(429) as err:
            svc = ai_health.report(err)
        self.assertTrue(svc)
        self.assertFalse(ai_health.available())

    def test_error_nuestro_no_baja(self):
        with self_http(400) as err:
            svc = ai_health.report(err)
        self.assertFalse(svc)
        self.assertTrue(ai_health.available())

    def test_exito_recupera(self):
        ai_health.mark_down("test")
        self.assertFalse(ai_health.available())
        ai_health.report(None)  # éxito
        self.assertTrue(ai_health.available())

    def test_forzar_down_pisa_todo(self):
        _env.set_env(self, TURNOS_AI_FORCE="down")
        self.assertFalse(ai_health.available())
        ai_health.mark_ok()  # no debería cambiar nada
        self.assertFalse(ai_health.available())

    def test_forzar_up_pisa_una_caida(self):
        ai_health.mark_down("test")
        _env.set_env(self, TURNOS_AI_FORCE="up")
        self.assertTrue(ai_health.available())


def self_http(code):
    return urllib.error.HTTPError("http://x", code, "msg", {}, io.BytesIO(b""))


# ---- endpoint /api/ai-status ------------------------------------------------
# HANDLER RETIREMENT (2026-08-30): el transporte legacy `handle()` fue
# eliminado de ai_health; los casos pegan al router nativo `ai_health_router`
# (montado solo en un FastAPI mínimo, mismo patrón que test_ai_native_routers.py)
# y fijan status/body con los valores exactos que el transporte retirado producía.

class EndpointTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        _no_forced_state(self)
        app = FastAPI()
        app.include_router(ai_health_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        _reset_state()

    def test_estado_disponible(self):
        r = self.client.get("/api/ai-status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"disponible": True})

    def test_estado_caido(self):
        ai_health.mark_down("cuota")
        r = self.client.get("/api/ai-status")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"disponible": False})

    def test_no_matchea_otras_rutas(self):
        # El router nativo sólo declara /api/ai-status: cualquier otra ruta
        # del montaje mínimo es 404 — el equivalente nativo del `False` que
        # el transporte retirado devolvía para rutas ajenas.
        r = self.client.get("/api/otra-cosa")
        self.assertEqual(r.status_code, 404)


# ---- ocr endpoint degrada cuando la IA está caída --------------------------

class OcrDegradaTestCase(unittest.TestCase):
    def setUp(self):
        _reset_state()
        from app.core import config
        self.addCleanup(config._reset_cache_para_tests)   # registered first: runs after the environment is put back
        _no_forced_state(self)
        _env.set_env(self, TURNOS_ENV_FILE=os.devnull)
        config._reset_cache_para_tests()
        # HANDLER RETIREMENT (2026-08-30): mismo cambio que EndpointTestCase
        # — el POST ahora pega al router nativo ocr_dni_router (mismo montaje
        # mínimo que test_cuentas_native_parity.py usa para este endpoint).
        _env.set_env(self, ENABLED_MODULES="turnos")
        app = FastAPI()
        app.include_router(ocr_dni_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        _reset_state()

    def _post(self, body):
        return self.client.post("/api/ocr-dni", json=body)

    def test_ia_caida_responde_no_disponible_sin_gastar(self):
        _env.set_env(self, TURNOS_AI_FORCE="down")
        r = self._post({"lado": "frente", "imageDataUrl": PNG_1x1})
        out = r.json()
        self.assertFalse(out["ok"])
        self.assertEqual(out.get("motivo"), "ia_no_disponible")


# ---- reproceso de lo acumulado (necesita base) -----------------------------

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_aihealth", seed=lambda: db_operators.operators_list())


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL, _MAINT_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL, _MAINT_URL = _db.test_url, _db.maint_url


def tearDownModule():
    _db.tear_down()


class ReprocesoTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_state()
        _no_forced_state(self)
        import psycopg
        from datetime import date
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
        # una solicitud que entró SIN análisis (IA estaba caída)
        self.tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        self.tmp.write(b"\x89PNG\r\n")  # basta un archivo real (FAKE ignora el contenido)
        self.tmp.close()
        self.sid = db_booking.create_booking_request("30111222", "Ana", "Lopez", date(1990, 1, 1),
                                      "wa:11", "svc-x", "centro-x", "image/png",
                                      solicitante_dni="30111222")
        db_booking.set_referral(self.sid, self.tmp.name, "image/png")
        db_booking.save_ai(self.sid, ai_referral.pending_ai_3pass())

    def tearDown(self):
        try:
            os.unlink(self.tmp.name)
        except OSError:
            pass
        _reset_state()

    def test_pendiente_ia_aparece_en_la_cola(self):
        filas = db_booking.booking_requests_pending_ai()
        self.assertEqual(len(filas), 1)
        self.assertEqual(filas[0]["id"], self.sid)
        self.assertEqual(db_booking.count_pending_ai(), 1)

    def test_reproceso_actualiza_el_ai_json(self):
        # FAKE_AI: analizar_orden_3pass devuelve 'parece_correcta' (filename sin
        # 'factura'). ai_health.available() = True por defecto → reprocesa.
        hechas = ai_health.reprocess_pending()
        self.assertEqual(hechas, 1)
        s = db_booking.get_booking_request(self.sid)
        self.assertNotEqual(s["ai_json"]["verdict"], "pendiente_ia")
        self.assertEqual(db_booking.count_pending_ai(), 0)

    def test_no_reprocesa_si_ia_caida(self):
        _env.set_env(self, TURNOS_AI_FORCE="down")
        self.assertEqual(ai_health.reprocess_pending(), 0)
        s = db_booking.get_booking_request(self.sid)
        self.assertEqual(s["ai_json"]["verdict"], "pendiente_ia")  # sigue en la cola


if __name__ == "__main__":
    unittest.main()
