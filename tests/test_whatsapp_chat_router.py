"""Native ASGI transport contract for the WhatsApp webhook.

All payloads and credentials in this file are synthetic. The suite blocks
network access and pins provider-visible status, body, and headers directly
against the native router; the retired dark transport is not reconstructed in
tests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
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

from app.core import app_modules, config  # noqa: E402

config.ENV_PATH = ""

from app.modules.chat import whatsapp_chat, whatsapp_chat_router  # noqa: E402


def _waha_message(**overrides):
    payload = {
        "id": "fixture-message",
        "from": "5492255000000@c.us",
        "fromMe": False,
        "body": "hello",
        "hasMedia": False,
    }
    payload.update(overrides)
    return {"event": "message", "session": "fixture", "payload": payload}


class TestNativeWhatsAppRouter(unittest.TestCase):
    def setUp(self):
        self.addCleanup(config._reset_cache_para_tests)   # registered first: runs after the environment is put back
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET="fixture-secret", META_WA_APP_SECRET=None,
                     ENABLED_MODULES="turnos,chat")
        config._reset_cache_para_tests()

        app = FastAPI()
        app.include_router(whatsapp_chat_router.router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()

    def test_meta_handshake_contract(self):
        accepted = self.client.get(
            "/api/whatsapp/webhook?hub.mode=subscribe"
            "&hub.verify_token=fixture-secret&hub.challenge=123456"
        )
        rejected = self.client.get(
            "/api/whatsapp/webhook?hub.mode=subscribe"
            "&hub.verify_token=wrong&hub.challenge=must-not-echo"
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.content, b"123456")
        self.assertTrue(accepted.headers["content-type"].startswith("text/plain"))
        self.assertEqual(rejected.status_code, 403)
        self.assertNotIn(b"must-not-echo", rejected.content)

    def test_trailing_slash_handshake_contract(self):
        path = (
            "/api/whatsapp/webhook/?hub.mode=subscribe"
            "&hub.verify_token=fixture-secret&hub.challenge=slash"
        )
        response = self.client.get(path, follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"slash")

    def test_bad_secret_and_malformed_json_contract(self):
        bad_secret = self.client.post(
            "/api/whatsapp/webhook", json=_waha_message(),
            headers={"X-Webhook-Secret": "wrong"},
        )
        malformed = self.client.post(
            "/api/whatsapp/webhook", content=b"{not-json",
            headers={"X-Webhook-Secret": "fixture-secret"},
        )
        self.assertEqual(bad_secret.status_code, 401)
        self.assertEqual(bad_secret.json(), {"error": "no_autorizado"})
        self.assertEqual(malformed.status_code, 200)
        self.assertEqual(malformed.json(), {"ok": True})

    def test_query_and_bearer_secret_forms_are_accepted(self):
        body = _waha_message(fromMe=True)
        cases = (
            ("/api/whatsapp/webhook?secret=fixture-secret", {}),
            ("/api/whatsapp/webhook", {"Authorization": "Bearer fixture-secret"}),
        )
        for path, headers in cases:
            with self.subTest(path=path, headers=tuple(headers)):
                response = self.client.post(path, json=body, headers=headers)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"ok": True})

    def test_text_and_all_media_kinds_process_once_without_network(self):
        kinds = ("text", "image", "document", "audio", "video", "sticker")
        for kind in kinds:
            with self.subTest(kind=kind):
                info = {"body": "hello"} if kind == "text" else {
                    "id": "fixture-media",
                    "mime_type": "application/octet-stream",
                    "filename": "fixture.bin",
                    "caption": "fixture caption",
                }
                body = {
                    "object": "whatsapp_business_account",
                    "entry": [{"changes": [{"field": "messages", "value": {
                        "messages": [{
                            "id": "fixture-event",
                            "from": "5492255000000",
                            "type": kind,
                            kind: info,
                        }],
                    }}]}],
                }
                calls = []
                with (
                    patch.object(whatsapp_chat, "_procesar_mensaje", calls.append),
                    patch.object(socket.socket, "connect", side_effect=AssertionError("network")),
                ):
                    response = self.client.post(
                        "/api/whatsapp/webhook",
                        json=body,
                        headers={"X-Webhook-Secret": "fixture-secret"},
                    )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"ok": True})
                self.assertEqual(len(calls), 1)

    def test_real_composition_claims_webhook_without_a_fallback_module(self):
        from app import main as app_module

        self.assertFalse(os.path.exists(os.path.join(ROOT, "app", "services", "legacy_proxy.py")))

        path = (
            "/api/whatsapp/webhook?hub.mode=subscribe"
            "&hub.verify_token=fixture-secret&hub.challenge=native"
        )
        client = TestClient(app_module.app)
        self.addCleanup(client.close)
        response = client.get(path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"native")

    def test_echo_group_and_status_payloads_are_acknowledged_without_processing(self):
        bodies = (
            _waha_message(fromMe=True),
            _waha_message(**{"from": "fixture@g.us"}),
            {"object": "whatsapp_business_account", "entry": [
                {"changes": [{"field": "statuses", "value": {"statuses": [{}]}}]},
            ]},
        )
        for body in bodies:
            with self.subTest(body=body):
                with patch.object(whatsapp_chat, "_procesar_mensaje") as process:
                    response = self.client.post(
                        "/api/whatsapp/webhook",
                        json=body,
                        headers={"X-Webhook-Secret": "fixture-secret"},
                    )
                self.assertEqual(response.status_code, 200)
                process.assert_not_called()

    def test_replayed_delivery_preserves_one_process_attempt_per_request(self):
        body = _waha_message()
        headers = {"X-Webhook-Secret": "fixture-secret"}
        calls = []
        with patch.object(whatsapp_chat, "_procesar_mensaje", calls.append):
            first = self.client.post("/api/whatsapp/webhook", json=body, headers=headers)
            replay = self.client.post("/api/whatsapp/webhook", json=body, headers=headers)
        self.assertEqual(first.json(), {"ok": True})
        self.assertEqual(replay.json(), {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_disabled_module_and_missing_secret_are_404(self):
        with patch.object(app_modules, "is_enabled", return_value=False):
            disabled = self.client.get("/api/whatsapp/webhook")
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET=None)
        config._reset_cache_para_tests()
        missing = self.client.get("/api/whatsapp/webhook")
        self.assertEqual(disabled.status_code, 404)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json(), {"error": "not found"})

    def test_meta_signs_the_raw_body_and_sends_no_shared_secret(self):
        """A POST shaped the way Meta ACTUALLY sends one: no X-Webhook-Secret,
        no Authorization, no ?secret= — only X-Hub-Signature-256 over the raw
        body. Before META_WA_APP_SECRET existed this 401'd, and the suite
        missed it because every Meta-shaped fixture carried a header Meta
        never sends."""
        raw = json.dumps({
            "object": "whatsapp_business_account",
            "entry": [{"changes": [{"field": "messages", "value": {"messages": [{
                "id": "fixture-signed", "from": "5492255000000",
                "type": "text", "text": {"body": "hola"},
            }]}}]}],
        }).encode("utf-8")
        firma = hmac.new(b"fixture-app-secret", raw, hashlib.sha256).hexdigest()
        _env.set_env(self, {"META_WA_APP_SECRET": "fixture-app-secret"})
        config._reset_cache_para_tests()

        calls = []
        with patch.object(whatsapp_chat, "_procesar_mensaje", calls.append):
            signed = self.client.post(
                "/api/whatsapp/webhook", content=raw,
                headers={"X-Hub-Signature-256": "sha256=" + firma,
                         "Content-Type": "application/json"},
            )
            # Same body, signature computed over DIFFERENT bytes -> rejected.
            tampered = self.client.post(
                "/api/whatsapp/webhook", content=raw.replace(b"hola", b"chau"),
                headers={"X-Hub-Signature-256": "sha256=" + firma,
                         "Content-Type": "application/json"},
            )
            missing_prefix = self.client.post(
                "/api/whatsapp/webhook", content=raw,
                headers={"X-Hub-Signature-256": firma,
                         "Content-Type": "application/json"},
            )
        self.assertEqual(signed.status_code, 200)
        self.assertEqual(tampered.status_code, 401)
        self.assertEqual(missing_prefix.status_code, 401)
        self.assertEqual(len(calls), 1)

    def test_app_secret_alone_keeps_the_endpoint_alive_but_not_the_handshake(self):
        """The 404 gate widens to "some credential exists", so an install that
        only has the app secret still answers POSTs. The GET handshake keeps
        needing the verify token — that IS the shared secret, by Meta's own
        registration flow."""
        _env.set_env(self, {"TURNOS_WA_WEBHOOK_SECRET": None, "META_WA_APP_SECRET": "fixture-app-secret"})
        config._reset_cache_para_tests()

        raw = json.dumps(_waha_message()).encode("utf-8")
        firma = hmac.new(b"fixture-app-secret", raw, hashlib.sha256).hexdigest()
        posted = self.client.post(
            "/api/whatsapp/webhook", content=raw,
            headers={"X-Hub-Signature-256": "sha256=" + firma,
                     "Content-Type": "application/json"},
        )
        handshake = self.client.get(
            "/api/whatsapp/webhook?hub.mode=subscribe"
            "&hub.verify_token=fixture-app-secret&hub.challenge=123",
        )
        self.assertEqual(posted.status_code, 200)
        self.assertEqual(handshake.status_code, 404)

    def test_no_credential_at_all_is_still_404(self):
        _env.set_env(self, TURNOS_WA_WEBHOOK_SECRET=None, META_WA_APP_SECRET=None)
        config._reset_cache_para_tests()
        response = self.client.post("/api/whatsapp/webhook", json=_waha_message())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": "not found"})

    def test_body_limit_contract(self):
        raw = b"{" + b" " * whatsapp_chat.MAX_WEBHOOK_BODY_BYTES + b"}"
        headers = {
            "X-Webhook-Secret": "fixture-secret",
            "Content-Length": str(len(raw)),
        }
        response = self.client.post("/api/whatsapp/webhook", content=raw, headers=headers)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json(), {"error": "body demasiado grande"})


if __name__ == "__main__":
    unittest.main()
