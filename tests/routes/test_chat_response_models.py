"""tests/routes/test_chat_response_models.py — every operation of the chat
module's JSON API (`tests/_insight_area.py`, part "chat") answered by the live
app, and the answer checked against the model the OpenAPI schema names for it
(TASK-76, OpenAPI stage D, area c): the success status the schema documents, a
JSON body that matches the model, and no top-level key the model does not
name.

The AI is the simulated one (`TURNOS_FAKE_AI=1`, the routes tier's default);
the WhatsApp number, the export token and the webhook secret are synthetic, and
the webhook gets an event that sends nothing out.
`test_every_operation_of_the_part_is_answered_here` keeps the list complete: a
new operation of the part needs its answer here.
"""

import base64
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_TEST_DIR = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_TEST_DIR)
for _p in (_ROOT, _HERE, _TEST_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _env  # noqa: E402
from _routes_fixture import RouteTestCase, admin_cookie  # noqa: E402
import _insight_area as area  # noqa: E402

from app.core import db_accounts  # noqa: E402

_DNI = "30999711"
_WA_NUMERO = "5492254000000"
_EXPORT_TOKEN = "fx-export-tok"
_WEBHOOK_SECRET = "fx-hook-secret"
_SESSION_ID = "c-fixture-0001"

# (method, path template) -> the test that answers it.
_ANSWERED = {}


def answers(*operations):
    def mark(fn):
        for operation in operations:
            _ANSWERED[operation] = fn.__name__
        return fn
    return mark


class TestChatAnswers(area.AnswerChecks, RouteTestCase):

    part = "chat"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.load_documented()

    def setUp(self):
        super().setUp()
        from app.core import config
        _env.set_env(self, TURNOS_WA_NUMERO=_WA_NUMERO, TURNOS_CHAT_EXPORT_TOKEN=_EXPORT_TOKEN,
                     TURNOS_WA_WEBHOOK_SECRET=_WEBHOOK_SECRET, META_WA_APP_SECRET=None)
        # Settings are cached for a few seconds: the class before this one read its own.
        config._reset_cache_para_tests()
        self.addCleanup(config._reset_cache_para_tests)

    def _patient(self):
        db_accounts.create_user(_DNI, "Marta", "Gomez", "1980-05-10", "F", "+542254000711",
                                "throwaway-12345")
        return {"Cookie": "st_ses=" + db_accounts.create_session(_DNI)}

    def test_every_operation_of_the_part_is_answered_here(self):
        """...or its answers are pinned in a golden file (checked by test_insight_response_docs.py)."""
        self.assert_every_operation_answered(_ANSWERED)

    @answers(("post", "/api/chat"), ("get", "/api/chat/history"), ("post", "/api/chat/new"),
             ("get", "/api/chat-transcripts"))
    def test_conversation(self):
        anonymous = self._answer("post", "/api/chat", body={"mensaje": "hola"})
        self.assertFalse(anonymous["autenticado"])
        self.assertEqual(anonymous["whatsapp"]["numero"], _WA_NUMERO)
        self.assertEqual(self._answer("get", "/api/chat/history"), {"mensajes": []})
        self.assertEqual(self._answer("post", "/api/chat/new"), {"ok": False})

        cookie = self._patient()
        turn = self._answer("post", "/api/chat", headers=cookie,
                            body={"mensaje": "quiero un turno de medicina general",
                                  "session_id": _SESSION_ID})
        self.assertTrue(turn["autenticado"])
        self.assertEqual(turn["session_id"], _SESSION_ID)
        self.assertTrue(turn["respuesta"])
        history = self._answer("get", "/api/chat/history", f"/api/chat/history?session_id={_SESSION_ID}",
                               headers=cookie)
        self.assertTrue(history["mensajes"])
        self.assertEqual(self._answer("post", "/api/chat/new", headers=cookie), {"ok": True})

        transcripts = self._answer("get", "/api/chat-transcripts", "/api/chat-transcripts?hours=1",
                                   headers={"Authorization": "Bearer " + _EXPORT_TOKEN})
        self.assertTrue(transcripts["conversaciones"])
        self.assertTrue(transcripts["conversaciones"][0]["mensajes"])

    @answers(("get", "/api/chat/whatsapp"))
    def test_whatsapp_link(self):
        link = self._answer("get", "/api/chat/whatsapp")
        self.assertEqual(link["numero"], _WA_NUMERO)
        self.assertNotIn("codigo", link)

    @answers(("get", "/api/chat/voice"), ("post", "/api/chat/transcribe"),
             ("get", "/api/admin/chat-voice"), ("post", "/api/admin/chat-voice"))
    def test_voice(self):
        self.assertEqual(self._answer("get", "/api/chat/voice"), {"disponible": True})
        audio = {"mime": "audio/webm", "data_b64": base64.b64encode(b"\x1aE\xdf\xa3fixture").decode("ascii")}
        self.assertTrue(self._answer("post", "/api/chat/transcribe", body={"audio": audio})["texto"])
        setting = self._answer("get", "/api/admin/chat-voice", headers=admin_cookie())
        self.assertTrue(setting["fal_configurado"])
        saved = self._answer("post", "/api/admin/chat-voice", headers=admin_cookie(),
                             body={"model": setting["default_model"]})
        self.assertEqual(saved, {"ok": True, "model": setting["default_model"]})

    @answers(("get", "/api/account/mcp"), ("get", "/api/account/mcp/"),
             ("post", "/api/account/mcp/regenerate"), ("post", "/api/account/mcp/regenerate/"))
    def test_mcp_connection(self):
        cookie = self._patient()
        first = self._answer("get", "/api/account/mcp", headers=cookie)
        self.assertTrue(first["token"])
        again = self._answer("get", "/api/account/mcp/", headers=cookie)
        self.assertIsNone(again["token"])
        self.assertEqual(again["hint"], first["hint"])
        rotated = self._answer("post", "/api/account/mcp/regenerate", headers=cookie)
        self.assertTrue(rotated["token"])
        self.assertNotEqual(rotated["token"], first["token"])
        self.assertTrue(self._answer("post", "/api/account/mcp/regenerate/", headers=cookie)["token"])

    @answers(("post", "/api/account/mcp/revoke"), ("post", "/api/account/mcp/revoke/"))
    def test_mcp_revoke(self):
        cookie = self._patient()
        self._answer("get", "/api/account/mcp", headers=cookie)
        self.assertTrue(self._answer("post", "/api/account/mcp/revoke", headers=cookie)["ok"])
        self.assertTrue(self._answer("post", "/api/account/mcp/revoke/", headers=cookie)["ok"])

    @answers(("post", "/api/whatsapp/webhook"), ("post", "/api/whatsapp/webhook/"))
    def test_whatsapp_webhook(self):
        event = {"event": "session.status", "session": "fixture", "payload": {"status": "WORKING"}}
        headers = {"X-Webhook-Secret": _WEBHOOK_SECRET}
        self.assertEqual(self._answer("post", "/api/whatsapp/webhook", headers=headers, body=event),
                         {"ok": True})
        self.assertEqual(self._answer("post", "/api/whatsapp/webhook/", headers=headers, body=event),
                         {"ok": True})


if __name__ == "__main__":
    unittest.main()
