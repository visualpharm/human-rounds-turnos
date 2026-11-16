"""test_voice_router.py — native ASGI transport contract for the three
paths of native-migration campaign family W2a (docs/roadmap/native-migration-campaign-
20260826.md): ``GET /api/chat/voice``, ``POST /api/chat/transcribe``, and
``GET``/``POST /api/admin/chat-voice``.

The suite pins the native router's responses directly for the paths that
don't need a real fal.ai call (born as byte-parity proofs against the
retired dark-handler transport ``voice.handle()``, deleted 2026-08-30),
and separately proves the admin gate now flows through
``core.deps.require_admin`` (401 with no session, same as every other
native admin endpoint — see ``core/deps.py``'s `AdminGateError`).
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import config, deps as core_deps  # noqa: E402

_ORIGINAL_ENV_PATH = config.ENV_PATH
config.ENV_PATH = ""

from app.modules.chat import voice, voice_router  # noqa: E402


def tearDownModule():
    config.ENV_PATH = _ORIGINAL_ENV_PATH
    config._reset_cache_para_tests()


class TestVoiceRouter(unittest.TestCase):
    def setUp(self):
        self.addCleanup(config._reset_cache_para_tests)  # registered first: runs once the env is back
        _env.set_env(self, FAL_KEY=None, ENABLED_MODULES="turnos,chat")
        config._reset_cache_para_tests()
        voice._HITS.clear()

        app = FastAPI()
        app.add_exception_handler(core_deps.AdminGateError, core_deps.render_admin_gate_error)
        app.include_router(voice_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_public_status_without_fal_key(self):
        with patch.object(voice, '_fake', return_value=False):
            native = self.client.get("/api/chat/voice")
        self.assertEqual(native.status_code, 200)
        self.assertEqual(native.json(), {"disponible": False})

    def test_public_status_fake_mode_has_microphone_without_fal_key(self):
        with patch.object(voice,'_fake',return_value=True):
            native = self.client.get('/api/chat/voice')
        self.assertEqual(native.json(), {'disponible':True})

    def test_transcribe_without_fal_key(self):
        # No FAL_KEY: `_audio()` still validates the shape first — an
        # obviously-invalid body reaches the 400 without needing a real
        # fal.ai call.
        body = json.dumps({"audio": {"mime": "audio/webm", "data_b64": ""}}).encode("utf-8")
        native = self.client.post("/api/chat/transcribe", content=body)
        self.assertEqual(native.status_code, 400)

    def test_admin_chat_voice_get_requires_session_same_as_other_native_admin_endpoints(self):
        native = self.client.get("/api/admin/chat-voice")
        self.assertEqual(native.status_code, 401)
        self.assertEqual(native.json(), {"error": "no_autenticado"})

    def test_admin_chat_voice_post_requires_session_same_as_other_native_admin_endpoints(self):
        native = self.client.post("/api/admin/chat-voice", json={"model": "fal-ai/wizper"})
        self.assertEqual(native.status_code, 401)
        self.assertEqual(native.json(), {"error": "no_autenticado"})


if __name__ == "__main__":
    unittest.main()
