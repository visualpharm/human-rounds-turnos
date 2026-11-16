"""`/admin/chats` — the chat module's read-only conversation list
(modules/chat/admin_chats_screen.py, product owner 2026-09-05).

Pins: admin-only under the chat-module gate (module off -> 404, medico ->
403), the rows reach the page as JSON for the client-side friendly-date
paint, a DB failure renders the failed line instead of a 500 shell, and the
sidebar shows the screen inside the Chat group next to the wiki.
"""

import datetime as dt
import json
import os
import re
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _env  # noqa: E402
_env.process_defaults(TURNOS_FAKE_AI="1")

from fastapi.testclient import TestClient  # noqa: E402

from app import main as app_module  # noqa: E402
from app.core import db  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.core import theme  # noqa: E402
from test_admin_screen_nav import _admin_cookies, _enabled_setup  # noqa: E402

_ROWS = [
    {"channel": "web", "key": "s-1", "started": dt.datetime(2026, 9, 5, 10, 0, tzinfo=dt.timezone.utc),
     "last_at": dt.datetime(2026, 9, 5, 10, 7, tzinfo=dt.timezone.utc), "user_messages": 3, "messages": 6,
     "first_message": "¿Puedo sacar turno para pediatría? </script><b>x</b>"},
    {"channel": "whatsapp", "key": "5492254123456", "started": dt.datetime(2026, 9, 4, 9, 0, tzinfo=dt.timezone.utc),
     "last_at": dt.datetime(2026, 9, 4, 9, 2, tzinfo=dt.timezone.utc), "user_messages": 1, "messages": 2,
     "first_message": "Hola"},
]


class TestAdminChatsScreen(unittest.TestCase):
    def setUp(self):
        self._set_enabled = _enabled_setup(self)
        self._set_enabled("turnos,intake,chat")
        self._theme_cache = dict(theme._cache)
        theme._cache.update({"v": "pinamar", "t": float("inf")})
        self.addCleanup(theme._cache.update, self._theme_cache)
        self.client = TestClient(app_module.app)

    def test_admin_gets_the_list_with_rows_as_json(self):
        with patch.object(db_chat, "chat_conversations", return_value=_ROWS), \
             patch.object(db_clinical, "insert_clinical_access_many"):
            r = self.client.get("/admin/chats", cookies=_admin_cookies("admin"))
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn('id="sec-chats"', r.text)
        self.assertIn('id="chats-rows"', r.text)
        self.assertIn('"user_messages": 3', r.text)
        self.assertIn("5492254123456", r.text)
        # a patient message can never close the JSON script block
        self.assertNotIn("</script><b>", r.text)
        self.assertIn("<\\/script>", r.text)
        # sidebar: the screen sits in the Chat group with the wiki
        self.assertIn('data-nav="chats"', r.text)
        self.assertIn('data-nav="knowledge"', r.text)
        self.assertIn('id="adm-nav-group-chat"', r.text)

    _INTERVIEW = {"channel": "interview", "key": "interview:42",
                  "started": dt.datetime(2026, 10, 23, 9, 0, tzinfo=dt.timezone.utc),
                  "last_at": dt.datetime(2026, 10, 23, 9, 5, tzinfo=dt.timezone.utc),
                  "user_messages": 4, "messages": 8,
                  "first_message": "Me duele el pecho desde ayer"}

    def test_an_interview_shown_in_the_list_lands_in_the_access_log(self):
        # K10 (2026-10-23): la lista mostraba el comienzo de la entrevista
        # previa —lo que el paciente contó de su salud— sin dejar rastro.
        from app.core import access_log
        access_log._seen.clear()
        with patch.object(db_chat, "chat_conversations", return_value=_ROWS + [self._INTERVIEW]), \
             patch.object(db_clinical, "interviews_by_session_keys",
                          return_value={"interview:42": {"id": 42, "dni": "30111222"}}) as owners, \
             patch.object(db_clinical, "insert_clinical_access_many") as log:
            r = self.client.get("/admin/chats", cookies=_admin_cookies("admin"))
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("Me duele el pecho", r.text)
        owners.assert_called_once_with(["interview:42"])
        actor, rol, action, rows = log.call_args.args
        self.assertEqual((rol, action), ("admin", "conversaciones"))
        self.assertEqual(rows[0], (access_log.dni_hash("30111222"), "42"))
        self.assertEqual({t.split(":")[0] for h, t in rows[1:]}, {"web", "whatsapp"})
        self.assertNotIn("30111222", repr(log.call_args))

    def test_web_and_whatsapp_rows_land_in_the_access_log_without_the_phone_number(self):
        # Security review 2026-11: only the interview rows were logged; the first 240 characters of a web
        # or WhatsApp conversation are the patient's words as well.
        from app.core import access_log
        access_log._seen.clear()
        with patch.object(db_chat, "chat_conversations", return_value=_ROWS), \
             patch.object(db_clinical, "insert_clinical_access_many") as log:
            r = self.client.get("/admin/chats", cookies=_admin_cookies("admin"))
        self.assertEqual(r.status_code, 200, r.text[:300])
        actor, rol, action, rows = log.call_args.args
        self.assertEqual((rol, action), ("admin", "conversaciones"))
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(h == "" and t.split(":")[0] in ("web", "whatsapp") for h, t in rows), rows)
        self.assertNotIn("5492254123456", repr(log.call_args))

    def test_a_web_list_that_cannot_be_logged_is_not_shown(self):
        from app.core import access_log
        access_log._seen.clear()
        with patch.object(db_chat, "chat_conversations", return_value=_ROWS), \
             patch.object(db_clinical, "insert_clinical_access_many", side_effect=db.DbUnavailable("x")):
            r = self.client.get("/admin/chats", cookies=_admin_cookies("admin"))
        self.assertIn('data-failed-flag="1"', r.text)
        self.assertNotIn("pediatría", r.text)

    def test_an_interview_list_that_cannot_be_logged_is_not_shown(self):
        from app.core import access_log
        access_log._seen.clear()
        with patch.object(db_chat, "chat_conversations", return_value=[self._INTERVIEW]), \
             patch.object(db_clinical, "interviews_by_session_keys",
                          return_value={"interview:42": {"id": 42, "dni": "30111222"}}), \
             patch.object(db_clinical, "insert_clinical_access_many", side_effect=db.DbUnavailable("x")):
            r = self.client.get("/admin/chats", cookies=_admin_cookies("admin"))
        self.assertEqual(r.status_code, 200)
        self.assertIn('data-failed-flag="1"', r.text)
        self.assertNotIn("Me duele el pecho", r.text)

    def test_last_activity_is_one_form_on_the_installation_clock(self):
        # 2026-11-07: the column mixed "Viernes 6 de noviembre, 21:58" (relativo
        # past six hours) and "Vie 6 nov, 17:21" (corta read off a UTC text).
        with patch.object(db_chat, "chat_conversations", return_value=[self._INTERVIEW]), \
             patch.object(db_clinical, "interviews_by_session_keys", return_value={}), \
             patch.object(db_clinical, "insert_clinical_access_many"):
            r = self.client.get("/admin/chats", cookies=_admin_cookies("admin"))
        self.assertEqual(r.status_code, 200, r.text[:300])
        rows = json.loads(re.search(r'id="chats-rows">(.*?)</script>', r.text, re.S).group(1))
        self.assertEqual(rows[0]["last_at"], "2026-10-23T06:05:00-03:00")
        self.assertEqual(rows[0]["started"], "2026-10-23T06:00:00-03:00")
        script = r.text.split('id="chats-rows"', 1)[1]
        self.assertIn("Dates.corta(", script)
        self.assertNotIn("Dates.relativo(", script)

    def test_db_failure_renders_the_failed_line_not_a_500(self):
        with patch.object(db_chat, "chat_conversations", side_effect=RuntimeError("down")):
            r = self.client.get("/admin/chats", cookies=_admin_cookies("admin"))
        self.assertEqual(r.status_code, 200)
        self.assertIn('data-failed-flag="1"', r.text)

    def test_rendered_formatter_imports_resolve_to_served_native_modules(self):
        with patch.object(db_chat, "chat_conversations", return_value=_ROWS):
            response = self.client.get("/admin/chats", cookies=_admin_cookies("admin"))
        self.assertEqual(response.status_code, 200)
        manifest = json.loads(re.search(r'window\.SANA_STATIC=(\{.*?\});</script>', response.text).group(1))
        for name, exported in (("dates", "corta"), ("format", "telTexto")):
            key = "platform/" + name + ".mjs"
            self.assertIn('window.SANA_STATIC["' + key + '"]', response.text)
            self.assertNotIn('window.SANA_STATIC["' + name + '.mjs"]', response.text)
            asset = self.client.get(manifest[key])
            self.assertEqual(asset.status_code, 200)
            self.assertIn(exported, asset.text)

    _CASO = {"channel": "caso", "key": "caso:tok1",
             "started": dt.datetime(2026, 10, 23, 8, 0, tzinfo=dt.timezone.utc),
             "last_at": dt.datetime(2026, 10, 23, 8, 5, tzinfo=dt.timezone.utc),
             "user_messages": 2, "messages": 4,
             "first_message": "Tengo fiebre hace tres dias"}

    def _custom_role(self, *perms):
        # A custom role lives in operator_access over the base value
        # `operators.role` can hold: the cookie carries that base, the store
        # answers the custom slug and its permissions (as in production).
        from app.core import permissions
        access = {"slug": "asistente_ia", "state": "ok", "permissions": frozenset(perms)}
        return patch.object(permissions, "access_for", return_value=access)

    @staticmethod
    def _store(rows):
        def chat_conversations(limit=200, days=90, exclude_channels=()):
            return [r for r in rows if r["channel"] not in exclude_channels]
        return patch.object(db_chat, "chat_conversations", side_effect=chat_conversations)

    @staticmethod
    def _CUSTOM():
        from app.core import permissions
        return _admin_cookies(permissions.BASE_ROLE_FOR_OTHER_SLUGS)

    def test_the_asistente_permission_alone_never_shows_an_interview(self):
        # Security review 2026-11, M-4: a role holding only `asistente` (edit the
        # assistant, read its conversations) read the start of every pre-consult
        # interview here, and of every «Contanos tu caso» chat (the same engine).
        # The web and WhatsApp rows stay; those go in the query itself (before
        # its limit), and nothing about them is looked up or logged.
        from app.core import access_log
        access_log._seen.clear()
        with self._custom_role("asistente"), \
             self._store(_ROWS + [self._INTERVIEW, self._CASO]) as store, \
             patch.object(db_clinical, "interviews_by_session_keys") as owners, \
             patch.object(db_clinical, "insert_clinical_access_many") as log:
            r = self.client.get("/admin/chats", cookies=self._CUSTOM())
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(set(store.call_args.kwargs["exclude_channels"]), {"interview", "caso"})
        self.assertNotIn("Me duele el pecho", r.text)
        self.assertNotIn("Tengo fiebre", r.text)
        self.assertNotIn("interview:42", r.text)
        self.assertIn("pediatría", r.text)
        owners.assert_not_called()
        rows = log.call_args.args[3]
        self.assertEqual({t.split(":")[0] for h, t in rows}, {"web", "whatsapp"})

    def test_a_role_that_reads_every_patient_still_sees_the_interview(self):
        from app.core import access_log
        access_log._seen.clear()
        with self._custom_role("asistente", "todos_los_pacientes"), \
             self._store([self._INTERVIEW, self._CASO]), \
             patch.object(db_clinical, "interviews_by_session_keys",
                          return_value={"interview:42": {"id": 42, "dni": "30111222"}}), \
             patch.object(db_clinical, "insert_clinical_access_many") as log:
            r = self.client.get("/admin/chats", cookies=self._CUSTOM())
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("Me duele el pecho", r.text)
        self.assertIn("Tengo fiebre", r.text)
        self.assertEqual(log.call_args.args[3][0], (access_log.dni_hash("30111222"), "42"))

    def test_the_admin_still_sees_the_interview(self):
        from app.core import access_log
        access_log._seen.clear()
        with self._store([self._INTERVIEW, self._CASO]) as store, \
             patch.object(db_clinical, "interviews_by_session_keys", return_value={}), \
             patch.object(db_clinical, "insert_clinical_access_many"):
            r = self.client.get("/admin/chats", cookies=_admin_cookies("admin"))
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertEqual(tuple(store.call_args.kwargs["exclude_channels"]), ())
        self.assertIn("Me duele el pecho", r.text)

    def test_the_asistente_permission_alone_reads_no_interview_api(self):
        # M-4: the routes that carry an interview's text (the doctor's consult,
        # the red alerts and their acknowledgement, the chart summary) refuse a
        # role holding only `asistente`; its own screens (the wiki, the prompts,
        # the interview switch per doctor) stay mapped to it.
        from app.core import permissions
        with self._custom_role("asistente"):
            for method, path in (("GET", "/api/admin/intake/consult/42"),
                                 ("GET", "/api/admin/intake/alerts"),
                                 ("GET", "/api/admin/intake/alerts/count"),
                                 ("POST", "/api/admin/intake/alerts/7/ack"),
                                 ("GET", "/api/admin/patient-summary?dni=30111222"),
                                 ("GET", "/admin/consult")):
                with self.subTest(path=path):
                    r = self.client.request(method, path, cookies=self._CUSTOM(),
                                            follow_redirects=False)
                    self.assertEqual(r.status_code, 403, r.text[:200])
        for template in ("/admin/knowledge", "/admin/prompts", "/admin/chats",
                         "/admin/entrevistas", "/api/admin/prompts", "/api/admin/intake/settings"):
            with self.subTest(template=template):
                self.assertTrue(permissions.holds(
                    permissions.required_permission("GET", template), {"asistente"}))

    def test_non_admin_roles_are_refused(self):
        with patch.object(db_chat, "chat_conversations", return_value=_ROWS):
            for rol in ("medico", "admisionista", "reportes"):
                with self.subTest(rol=rol):
                    r = self.client.get("/admin/chats", cookies=_admin_cookies(rol))
                    self.assertEqual(r.status_code, 403, r.text[:200])
            # No-session on HTML screen: 303 redirect (B9 fix)
            r = self.client.get("/admin/chats", follow_redirects=False)
            self.assertIn(r.status_code, (303,))

    def test_module_off_is_404(self):
        self._set_enabled("turnos,intake")
        r = self.client.get("/admin/chats", cookies=_admin_cookies("admin"))
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
