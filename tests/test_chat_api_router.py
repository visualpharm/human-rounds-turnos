"""test_chat_api_router.py — native ASGI transport contract for the paths
of native-migration campaign family W2a (docs/roadmap/native-migration-campaign-
20260826.md, wave 2): ``POST /api/chat``, ``GET /api/chat/
whatsapp``, ``GET /api/chat/history``, ``POST /api/chat/new`` («Nuevo chat»,
2026-09-11), ``GET /api/chat-transcripts``.

All payloads/tokens here are synthetic (``TURNOS_FAKE_AI=1``). Born as
byte-parity proofs against the retired dark-handler transport
(``chat_api.handle()``, deleted 2026-08-30); the assertions now pin the
native responses directly. A "feature not configured" 404 (no
``TURNOS_WA_NUMERO`` / no ``TURNOS_CHAT_EXPORT_TOKEN``) is the SHARED
terminal 404 (``{"error": "not found"}``): the legacy transport DECLINED
those paths and the dispatcher fell to the terminal — deployed main serves
exactly those bytes, and the final wave aligned the native route (an
interim "clean domain-404" ``{"error": "no_encontrado"}`` shape was
superseded).
"""

from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import config  # noqa: E402

_ORIGINAL_ENV_PATH = config.ENV_PATH
config.ENV_PATH = ""

from app.modules.chat import chat_api, chat_api_router  # noqa: E402


def tearDownModule():
    config.ENV_PATH = _ORIGINAL_ENV_PATH
    config._reset_cache_para_tests()


class TestChatApiRouter(unittest.TestCase):
    def setUp(self):
        self.addCleanup(config._reset_cache_para_tests)  # registered first: runs once the env is back
        _env.set_env(self, ENABLED_MODULES="turnos,chat")
        config._reset_cache_para_tests()
        chat_api._HITS.clear()

        app = FastAPI()
        app.include_router(chat_api_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_post_chat_anonymous_responds(self):
        body = json.dumps({"mensaje": "hola"}).encode("utf-8")
        native = self.client.post("/api/chat", content=body)
        self.assertEqual(native.status_code, 200)
        self.assertIn("respuesta", native.json())
        self.assertFalse(native.json()["autenticado"])

    def test_get_whatsapp_when_configured(self):
        _env.set_env(self, TURNOS_WA_NUMERO="5491100000000")
        native = self.client.get("/api/chat/whatsapp")
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json()["numero"], "5491100000000")

    def test_get_whatsapp_unconfigured_is_the_shared_terminal_404(self):
        # Legacy _whatsapp returned False (declined) with no number
        # configured and the dispatcher fell to the terminal 404 — deployed
        # main serves exactly these bytes (final wave).
        _env.set_env(self, TURNOS_WA_NUMERO=None)
        config._reset_cache_para_tests()
        native = self.client.get("/api/chat/whatsapp")
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.content, b'{"error": "not found"}')

    def test_get_history_anonymous(self):
        native = self.client.get("/api/chat/history")
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json(), {"mensajes": []})

    def test_post_chat_new_anonymous_is_a_noop(self):
        # «Nuevo chat» (2026-09-11): the widget posts fire-and-forget; with
        # no session it writes NOTHING and answers {"ok": false} with 200 —
        # never an error (same identity contract as get_history above).
        native = self.client.post("/api/chat/new")
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json(), {"ok": False})

    def test_get_transcripts_when_configured(self):
        _env.set_env(self, TURNOS_CHAT_EXPORT_TOKEN="fixture-token")
        config._reset_cache_para_tests()
        native = self.client.get(
            "/api/chat-transcripts", headers={"Authorization": "Bearer fixture-token"})
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json(), {"conversaciones": []})

    def test_get_transcripts_bad_bearer(self):
        _env.set_env(self, TURNOS_CHAT_EXPORT_TOKEN="fixture-token")
        config._reset_cache_para_tests()
        native = self.client.get(
            "/api/chat-transcripts", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(native.status_code, 403)
        self.assertEqual(native.json(), {"error": "no_autorizado"})

    def test_get_transcripts_unconfigured_is_the_shared_terminal_404(self):
        # Same decline contract as the unconfigured WhatsApp link above.
        _env.set_env(self, TURNOS_CHAT_EXPORT_TOKEN=None)
        config._reset_cache_para_tests()
        native = self.client.get("/api/chat-transcripts")
        self.assertEqual(native.status_code, 404)
        self.assertEqual(native.content, b'{"error": "not found"}')


if __name__ == "__main__":
    unittest.main()
