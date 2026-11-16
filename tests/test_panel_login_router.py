"""test_panel_login_router.py — native ASGI transport parity for
/api/login-panel + /api/login-panel/event (docs/roadmap/native-migration-
campaign-20260826.md, family F9). test_panel_login.py already covers the
domain behavior (handle()/db.py); this file only proves the FastAPI router
serves the exact same status/body/header contract, with no DB required."""

import json
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


class TestPanelLoginRouter(unittest.TestCase):
    def setUp(self):
        config._reset_cache_para_tests()
        self.client = TestClient(app_module.app)
        self.addCleanup(config._reset_cache_para_tests)

    def test_contenido_is_native_json_with_shape(self):
        r = self.client.get("/api/login-panel")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/json; charset=utf-8")
        self.assertEqual(r.headers["cache-control"], "no-store")
        self.assertIn("humor", r.json())
        self.assertIn("consejos", r.json())
        self.assertEqual(r.content, json.dumps(r.json(), ensure_ascii=False).encode("utf-8"))

    def test_evento_valido_responde_204_sin_body(self):
        payload = {"evento": "exposicion", "variante": "humor", "sid": "cafecafe"}
        r = self.client.post("/api/login-panel/event", json=payload)
        self.assertEqual(r.status_code, 204)
        self.assertEqual(r.content, b"")

    def test_evento_invalido_responde_400_con_mensaje_exacto(self):
        r = self.client.post("/api/login-panel/event", json={"evento": "no-existe"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "evento inválido"})

    def test_body_no_json_responde_400(self):
        r = self.client.post(
            "/api/login-panel/event", content=b"{not json",
            headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {"error": "JSON inválido"})


if __name__ == "__main__":
    unittest.main()
