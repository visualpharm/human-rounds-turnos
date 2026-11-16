"""test_puco_router.py — native ASGI transport parity for POST /api/puco
(docs/roadmap/native-migration-campaign-20260826.md, family F9).
test_puco.py already covers the domain logic (`puco.consultar`/`_dedup`/etc.
against fake mode and cassettes); this file only proves the FastAPI router
serves the exact same status/body/header contract, offline (TURNOS_FAKE_PUCO)."""

import json
import os
import sys
import unittest

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")
_env.process_set(TURNOS_FAKE_PUCO="1")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import config  # noqa: E402
config.ENV_PATH = ""

from app import main as app_module  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class TestPucoRouter(unittest.TestCase):
    def setUp(self):
        # Sibling test modules (test_puco.py) flip TURNOS_FAKE_PUCO back to
        # "0" in their own setUp without restoring it — reassert it here so
        # this file's outcome doesn't depend on collection order.
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, TURNOS_FAKE_PUCO="1")
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)

    def test_dni_invalido_responde_400(self):
        r = self.client.post("/api/puco", json={"dni": "123"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "DNI inválido"})
        self.assertEqual(r.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(r.headers["cache-control"], "no-store")

    def test_dni_valido_fake_mode_devuelve_cobertura(self):
        r = self.client.post("/api/puco", json={"dni": "30123454"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["disponible"])
        self.assertEqual(len(body["obras_sociales"]), 2)  # último dígito "4": IOMA + OSECAC
        self.assertEqual(r.content, json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def test_bad_json_body_returns_400(self):
        r = self.client.post(
            "/api/puco", content=b"{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "JSON inválido"})


if __name__ == "__main__":
    unittest.main()
