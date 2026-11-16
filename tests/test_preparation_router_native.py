"""test_preparation_router_native.py — native-transport parity for
preparation_router.py's two GET routes (/upload-order,
/api/preparation-studies). Both underlying functions
(`preparation._upload_order_page`/`preparation._studies`) are pure catalog
reads — no database involved at all, so these run fully offline.

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


class TestPreparationRouterNative(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_upload_order_sirve_html(self):
        response = self.client.get("/upload-order")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertNotIn("cache-control", {k.lower() for k in response.headers.keys()})
        html = response.text
        self.assertIn('id="prep-title"', html)
        self.assertIn('id="prep-sub"', html)

    def test_upload_order_con_servicio_en_breadcrumb(self):
        response = self.client.get("/upload-order", params={"service": "ecografia-hospital"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('svc-crumb', response.text)

    def test_api_preparation_studies_200_json(self):
        response = self.client.get("/api/preparation-studies")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn("estudios", response.json())
        self.assertTrue(response.json()["estudios"])
        import json
        self.assertEqual(response.content, json.dumps(response.json(), ensure_ascii=False).encode("utf-8"))

    def test_metodo_post_no_soportado_cae_al_404_terminal(self):
        response = self.client.post("/api/preparation-studies")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content, b'{"error": "not found"}')


if __name__ == "__main__":
    unittest.main()
