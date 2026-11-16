"""test_whatsapp_routing.py — Meta posts every reply to ONE callback per app; the
demo and pinamar.humanrounds.org share that app and number, so a «Sí/No» to a
demo reminder lands on pinamar's webhook (and could confirm the wrong turno).

Each installation processes only the replies that are its own:
- `TURNOS_WA_FORWARD_NUMBERS` + `TURNOS_WA_FORWARD_URL`: messages from those
  numbers belong to the sibling installation, are forwarded (re-signed, marked)
  and never processed here;
- a demo with a recipient list processes only its recipients.

Correr:  python3 -m unittest discover -s tests -p "test_whatsapp_routing.py"
"""

import copy
import hashlib
import hmac
import json
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import config  # noqa: E402
from app.modules.chat import whatsapp_chat, whatsapp_chat_router  # noqa: E402

_DEMO_TEL = "5492255111222"
_OWN_TEL = "5492255333444"
_URL = "https://demo.example.org/api/whatsapp/webhook"
_APP_SECRET = "app-secret-x"


def _meta(*tels):
    messages = [{"from": t, "id": "wamid." + t, "type": "button",
                 "button": {"text": "Sí, voy"}} for t in tels]
    return {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [
        {"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "1309146762272339"},
            "messages": messages}}]}]}


class RoutingCase(unittest.TestCase):
    def setUp(self):
        self.addCleanup(config._reset_cache_para_tests)
        _env.set_env(self, ENABLED_MODULES="turnos,chat", TURNOS_WA_WEBHOOK_SECRET="s",
                     META_WA_APP_SECRET=_APP_SECRET)
        config._reset_cache_para_tests()
        self.local = []
        self.forwarded = []
        p1 = patch.object(whatsapp_chat, "_procesar_mensaje", side_effect=self.local.append)
        p2 = patch.object(whatsapp_chat, "_send_forward",
                          side_effect=lambda url, raw, headers: self.forwarded.append((url, raw, headers)) or True)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def _siblings(self):
        _env.set_env(self, TURNOS_WA_FORWARD_URL=_URL, TURNOS_WA_FORWARD_NUMBERS="+54 9 2255 111222, 549226000000")
        config._reset_cache_para_tests()


class TestPinamarForwardsDemoReplies(RoutingCase):
    def test_reply_of_a_demo_recipient_is_forwarded_and_not_processed_here(self):
        self._siblings()
        whatsapp_chat.process_webhook_payload(_meta(_DEMO_TEL))
        self.assertEqual(self.local, [])
        self.assertEqual(len(self.forwarded), 1)
        self.assertEqual(self.forwarded[0][0], _URL)

    def test_own_reply_is_processed_here_and_not_forwarded(self):
        self._siblings()
        whatsapp_chat.process_webhook_payload(_meta(_OWN_TEL))
        self.assertEqual([m["from"] for m in self.local], [_OWN_TEL])
        self.assertEqual(self.forwarded, [])

    def test_mixed_envelope_is_split_and_the_forward_is_resigned_and_marked(self):
        self._siblings()
        whatsapp_chat.process_webhook_payload(_meta(_OWN_TEL, _DEMO_TEL))
        self.assertEqual([m["from"] for m in self.local], [_OWN_TEL])
        _url, raw, headers = self.forwarded[0]
        sent = json.loads(raw)
        tels = [m["from"] for e in sent["entry"] for c in e["changes"] for m in c["value"]["messages"]]
        self.assertEqual(tels, [_DEMO_TEL])
        want = "sha256=" + hmac.new(_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-Hub-Signature-256"], want)
        self.assertEqual(headers[whatsapp_chat.FORWARD_HEADER], "1")

    def test_a_forwarded_request_is_never_forwarded_again(self):
        self._siblings()
        whatsapp_chat.process_webhook_payload(_meta(_DEMO_TEL), forwarded=True)
        self.assertEqual(self.forwarded, [])

    def test_without_forward_settings_nothing_changes(self):
        whatsapp_chat.process_webhook_payload(_meta(_DEMO_TEL, _OWN_TEL))
        self.assertEqual(len(self.local), 2)
        self.assertEqual(self.forwarded, [])

    def test_the_input_envelope_is_not_mutated(self):
        self._siblings()
        body = _meta(_OWN_TEL, _DEMO_TEL)
        before = copy.deepcopy(body)
        whatsapp_chat.process_webhook_payload(body)
        self.assertEqual(body, before)

    def test_internal_docker_alias_over_http_is_used(self):
        _env.set_env(self, TURNOS_WA_FORWARD_URL="http://turnos-demo:7700/api/whatsapp/webhook",
                     TURNOS_WA_FORWARD_NUMBERS=_DEMO_TEL)
        config._reset_cache_para_tests()
        whatsapp_chat.process_webhook_payload(_meta(_DEMO_TEL))
        self.assertEqual(len(self.forwarded), 1)

    def test_only_https_urls_are_used(self):
        for url in ("file:///etc/passwd", "http://demo.example.org/api/whatsapp/webhook"):
            with self.subTest(url=url):
                _env.set_env(self, TURNOS_WA_FORWARD_URL=url, TURNOS_WA_FORWARD_NUMBERS=_DEMO_TEL)
                config._reset_cache_para_tests()
                whatsapp_chat.process_webhook_payload(_meta(_DEMO_TEL))
                self.assertEqual(self.forwarded, [])


class TestDemoProcessesOnlyItsRecipients(RoutingCase):
    def test_reply_from_outside_the_recipient_list_is_dropped(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1", TURNOS_WA_TEST_RECIPIENTS="+54 9 2255 111222")
        config._reset_cache_para_tests()
        whatsapp_chat.process_webhook_payload(_meta(_OWN_TEL, _DEMO_TEL))
        self.assertEqual([m["from"] for m in self.local], [_DEMO_TEL])

    def test_demo_without_a_list_keeps_processing_everything(self):
        _env.set_env(self, TURNOS_DEMO_MODE="1")
        config._reset_cache_para_tests()
        whatsapp_chat.process_webhook_payload(_meta(_OWN_TEL))
        self.assertEqual(len(self.local), 1)

    def test_a_non_demo_installation_ignores_the_recipient_list_for_inbound(self):
        _env.set_env(self, TURNOS_WA_TEST_RECIPIENTS="+54 9 2255 111222")
        config._reset_cache_para_tests()
        whatsapp_chat.process_webhook_payload(_meta(_OWN_TEL))
        self.assertEqual(len(self.local), 1)


class TestRouterMarksForwardedRequests(RoutingCase):
    def test_the_marker_header_reaches_the_processor(self):
        app = FastAPI()
        app.include_router(whatsapp_chat_router.router)
        client = TestClient(app)
        self.addCleanup(client.close)
        seen = []
        with patch.object(whatsapp_chat, "process_webhook_payload",
                          side_effect=lambda body, forwarded=False: seen.append(forwarded)):
            client.post("/api/whatsapp/webhook", json=_meta(_DEMO_TEL), headers={"X-Webhook-Secret": "s"})
            client.post("/api/whatsapp/webhook", json=_meta(_DEMO_TEL),
                        headers={"X-Webhook-Secret": "s", whatsapp_chat.FORWARD_HEADER: "1"})
        self.assertEqual(seen, [False, True])


if __name__ == "__main__":
    unittest.main()
