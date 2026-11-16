"""Native transport parity for the platform module registry APIs."""

import json
import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="")
_env.process_defaults(TURNOS_FAKE_AI="1")
_env.process_defaults(TURNOS_SKIP_HSI="1")
_env.process_defaults(TURNOS_ADMIN_PASS="module-router-test-pass")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from app.auth import admin_auth  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _cookies(role="admin"):
    return {"st_admin": admin_auth._mint_token("module-router-test", role)}


class TestAppModulesRouter(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_admin_modules_preserves_role_filtered_payload_and_json_shape(self):
        self.assertEqual(self.client.get("/api/admin/modules").status_code, 401)
        admin = self.client.get("/api/admin/modules", cookies=_cookies())
        medico = self.client.get("/api/admin/modules", cookies=_cookies("medico"))
        denied = self.client.get("/api/admin/modules", cookies=_cookies("reportes"))
        self.assertEqual(admin.status_code, 200)
        self.assertEqual(medico.status_code, 200)
        self.assertEqual(denied.status_code, 403)
        self.assertIn("modules", admin.json())
        self.assertNotIn("modules", medico.json())
        self.assertIn("sections", medico.json())
        self.assertEqual(denied.json(), {"error": "rol_no_autorizado"})
        self.assertEqual(admin.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(admin.headers["cache-control"], "no-store")
        self.assertEqual(admin.content, json.dumps(admin.json(), ensure_ascii=False).encode("utf-8"))

    def test_public_nav_is_native_and_post_stays_unmatched(self):
        response = self.client.get("/api/nav")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json()["nav_entries"], list)
        self.assertEqual(response.headers["cache-control"], "no-store")
        post = self.client.post("/api/nav", json={})
        self.assertEqual(post.status_code, 404)
        self.assertEqual(post.content, b'{"error": "not found"}')


if __name__ == "__main__":
    unittest.main()
