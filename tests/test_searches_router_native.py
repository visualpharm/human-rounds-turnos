"""test_searches_router_native.py — native-transport parity for
searches_router.py's single route (POST /api/search-log). Offline: no
managed Postgres — DATABASE_URL="" means `db_analytics.log_search` no-ops immediately
(DbUnavailable caught internally, see core/db.py), so the 204 contract holds
even without a database, matching test_searches.py's own
TestDbCaidaNoRompe.test_endpoint_sigue_devolviendo_204.

Same env/import bootstrap as tests/test_app_modules_router.py.
"""

import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.modules.turnos import searches  # noqa: E402


class TestSearchesRouterNative(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)
        searches._HITS.clear()

    def tearDown(self):
        searches._HITS.clear()

    def test_ok_204_sin_body_ni_content_type(self):
        response = self.client.post(
            "/api/search-log",
            json={"q": "cardiologia", "resultados": 3, "origen": "/doctors"},
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertNotIn("content-type", {k.lower() for k in response.headers.keys()})

    def test_q_corto_400(self):
        response = self.client.post("/api/search-log", json={"q": "a", "resultados": 0})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "q inválido"})
        self.assertEqual(response.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_resultados_invalido_400(self):
        response = self.client.post(
            "/api/search-log", json={"q": "cardiologia", "resultados": "no-numero"})
        # The strict body boundary (TASK-76 item F) answers it before the domain's own «resultados inválido».
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "JSON inválido: campo resultados: se esperaba un número entero"})

    def test_json_invalido_400(self):
        response = self.client.post(
            "/api/search-log", content=b"{no es json",
            headers={"Content-Type": "application/json"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "JSON inválido"})

    def test_rate_limit_por_ip_sigue_devolviendo_204(self):
        """El tope de analytics (`allowed`) nunca se le avisa al cliente —
        sigue siendo 204 aunque no se haya persistido nada (misma garantía
        `_no_content` de siempre)."""
        _env.set_env(self, TURNOS_SEARCHES_PER_DAY="1")
        first = self.client.post(
            "/api/search-log", json={"q": "kinesiologia", "resultados": 2},
            headers={"X-Forwarded-For": "203.0.113.9"})
        second = self.client.post(
            "/api/search-log", json={"q": "otra query distinta", "resultados": 1},
            headers={"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(first.status_code, 204)
        self.assertEqual(second.status_code, 204)

    def test_metodo_get_no_soportado_cae_al_404_terminal(self):
        response = self.client.get("/api/search-log")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b'{"error": "not found"}')


if __name__ == "__main__":
    unittest.main()
