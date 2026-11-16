"""test_email_transport.py — the email transport is a connector capability
(`email_transport`): Mailgun stays the default, Mailjet is selectable with
CONNECTOR_EMAIL_TRANSPORT=mailjet (salud.pinamar.gob.ar, 2026-10-20).

Pins:
1. Nothing configured → every caller's `notifications.mail.send` reaches
   `notifications.mailgun.send`, exactly as before.
2. Mailjet selected → the Send API v3.1 request is the one recorded in
   tests/cassettes/mailjet/send_ok.json (replay matches the whole `Messages`
   list), with Basic auth from MAILJET_API_KEY/MAILJET_API_SECRET.
3. Degrade, never fail: no keys, MAIL_ENABLED=0, `off`, a typo, a provider
   error → False, no network, never raises.
4. Settings: the Mailjet keys are masked, the provider is a closed choice,
   and «Probar» checks the SELECTED provider — Mailgun in its configured
   region (MAILGUN_API_BASE), not always the US one.
5. «Sistema» describes the selected provider.

Correr:  TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_email_transport.py"
"""

import base64
import importlib.util
import json
import os
import sys
import unittest
import urllib.request
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
CASSETTES_DIR = os.path.join(ROOT, "tests", "cassettes")

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # never read the dev checkout's real .env

from app import connectors  # noqa: E402
from app.notifications import mail  # noqa: E402
from app.notifications import mailgun  # noqa: E402
from app.connectors.mailjet import mailjet  # noqa: E402
from app.admin_panel import settings_admin as sa  # noqa: E402
from app.admin_panel import system_status  # noqa: E402

_vcr_spec = importlib.util.spec_from_file_location("_vcr_mailjet", os.path.join(CASSETTES_DIR, "_vcr.py"))
_vcr = importlib.util.module_from_spec(_vcr_spec)
sys.modules["_vcr_mailjet"] = _vcr
_vcr_spec.loader.exec_module(_vcr)

_KEYS = ("CONNECTOR_EMAIL_TRANSPORT", "MAILJET_API_KEY", "MAILJET_API_SECRET",
         "MAILGUN_API_KEY", "MAILGUN_DOMAIN", "MAILGUN_API_BASE",
         "MAIL_FROM", "MAIL_REPLY_TO", "MAIL_ENABLED")

_MJ_ENV = {
    "CONNECTOR_EMAIL_TRANSPORT": "mailjet",
    "MAILJET_API_KEY": "mj-public-test",
    "MAILJET_API_SECRET": "mj-private-test-9f3e",
    "MAIL_FROM": "Salud Pinamar <turnos@mj.salud.example.test>",
    "MAIL_REPLY_TO": "salud@example.test",
}


class _Base(unittest.TestCase):
    def setUp(self):
        self.addCleanup(config._reset_cache_para_tests)   # registered first: runs after the environment is put back
        _env.set_env(self, dict.fromkeys(_KEYS))
        p = mock.patch.object(config, "_db_overrides", return_value={})
        p.start()
        self.addCleanup(p.stop)
        config._reset_cache_para_tests()

    def env(self, **values):
        _env.set_env(self, values)
        config._reset_cache_para_tests()

    def no_network(self):
        """Any outbound call fails the test (both stdlib dispatch paths)."""
        def boom(*a, **k):
            raise AssertionError("network call where none was expected")
        p1 = mock.patch.object(urllib.request, "urlopen", boom)
        p2 = mock.patch.object(urllib.request.OpenerDirector, "open", boom)
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)


class TestSelection(_Base):
    def test_unconfigured_installation_resolves_mailgun(self):
        self.assertEqual(connectors.resolve(connectors.EMAIL_TRANSPORT), ("mailgun", "default"))

    def test_default_dispatch_reaches_mailgun_send(self):
        with mock.patch("app.notifications.mailgun.send", return_value=True) as mg, \
                mock.patch("app.connectors.mailjet.mailjet.send") as mj:
            self.assertTrue(mail.send("p@example.test", "Asunto", "Cuerpo", html="<p>x</p>"))
        mg.assert_called_once_with("p@example.test", "Asunto", "Cuerpo", html="<p>x</p>")
        mj.assert_not_called()

    def test_mailjet_selected_never_touches_mailgun(self):
        self.env(**_MJ_ENV)
        with mock.patch("app.notifications.mailgun.send") as mg, \
                mock.patch("app.connectors.mailjet.mailjet.send", return_value=True) as mj:
            self.assertTrue(mail.send("p@example.test", "Asunto", "Cuerpo"))
        mj.assert_called_once_with("p@example.test", "Asunto", "Cuerpo")
        mg.assert_not_called()

    def test_text_only_send_keeps_the_three_argument_call(self):
        # Suites patch the transport with 3-argument fakes; a text-only email
        # (staff invitation, reset, disk alert) must not grow an html= kwarg.
        def fake(to, subject, text):
            return True
        with mock.patch("app.notifications.mailgun.send", side_effect=fake):
            self.assertTrue(mail.send("p@example.test", "Asunto", "Cuerpo"))

    def test_off_and_typo_send_nothing(self):
        self.no_network()
        for value in ("off", "mailjett"):
            with self.subTest(value=value):
                self.env(CONNECTOR_EMAIL_TRANSPORT=value)
                with mock.patch("app.notifications.mailgun.send") as mg:
                    self.assertFalse(mail.send("p@example.test", "A", "B"))
                mg.assert_not_called()

    def test_transport_exception_never_escapes(self):
        with mock.patch("app.notifications.mailgun.send", side_effect=RuntimeError("down")):
            self.assertFalse(mail.send("p@example.test", "A", "B"))


class TestMailjetSend(_Base):
    def test_replay_matches_the_recorded_request(self):
        self.env(**_MJ_ENV)
        with _vcr.replay("mailjet", "send_ok"):
            ok = mail.send("paciente@example.test", "Tu turno",
                           "Tu turno es el 9 de julio a las 09:00.",
                           html="<p>Tu turno es el 9 de julio a las 09:00.</p>")
        self.assertTrue(ok)

    def test_basic_auth_is_key_and_secret(self):
        self.env(**_MJ_ENV)
        seen = {}

        class _Resp:
            status = 200
            def read(self):
                return json.dumps({"Messages": [{"Status": "success"}]}).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            seen["url"] = req.full_url
            seen["auth"] = req.get_header("Authorization")
            return _Resp()

        with mock.patch.object(urllib.request, "urlopen", fake_urlopen):
            self.assertTrue(mail.send("paciente@example.test", "A", "B"))
        self.assertEqual(seen["url"], "https://api.mailjet.com/v3.1/send")
        expected = base64.b64encode(b"mj-public-test:mj-private-test-9f3e").decode()
        self.assertEqual(seen["auth"], "Basic " + expected)

    def test_message_level_error_is_a_failed_send(self):
        self.env(**_MJ_ENV)

        class _Resp:
            status = 200
            def read(self):
                return json.dumps({"Messages": [{"Status": "error", "Errors": [{"ErrorCode": "send-0003"}]}]}).encode()
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        with mock.patch.object(urllib.request, "urlopen", lambda *a, **k: _Resp()):
            self.assertFalse(mail.send("paciente@example.test", "A", "B"))

    def test_http_error_is_a_failed_send(self):
        self.env(**_MJ_ENV)
        tmp = os.path.join(CASSETTES_DIR, "mailjet", "send_ok.json")
        with open(tmp, encoding="utf-8") as fh:
            data = json.load(fh)
        data["interactions"][0]["response"]["status"] = 401
        data["interactions"][0]["response"]["body"] = {"ErrorMessage": "API key authentication/authorization failure"}
        import tempfile, shutil
        scratch = tempfile.mkdtemp(prefix="mailjet_cassette_")
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        os.makedirs(os.path.join(scratch, "mailjet"))
        shutil.copy(os.path.join(CASSETTES_DIR, "mailjet", "fields.py"), os.path.join(scratch, "mailjet", "fields.py"))
        with open(os.path.join(scratch, "mailjet", "send_ok.json"), "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        with mock.patch("_vcr_mailjet._cassette_dir", lambda c: os.path.join(scratch, c)):
            with _vcr.replay("mailjet", "send_ok"):
                ok = mail.send("paciente@example.test", "Tu turno",
                               "Tu turno es el 9 de julio a las 09:00.",
                               html="<p>Tu turno es el 9 de julio a las 09:00.</p>")
        self.assertFalse(ok)

    def test_missing_keys_or_sender_is_a_silent_skip(self):
        self.no_network()
        for drop in ("MAILJET_API_KEY", "MAILJET_API_SECRET", "MAIL_FROM"):
            with self.subTest(missing=drop):
                env = dict(_MJ_ENV)
                env.pop(drop)
                self.env(**{**dict.fromkeys(_KEYS), **env})
                self.assertFalse(mail.send("p@example.test", "A", "B"))

    def test_mail_enabled_zero_disables_mailjet_too(self):
        self.no_network()
        self.env(MAIL_ENABLED="0", **_MJ_ENV)
        self.assertFalse(mail.send("p@example.test", "A", "B"))


class TestMailgunRegion(_Base):
    def test_send_uses_the_configured_api_base_at_call_time(self):
        self.env(MAILGUN_API_KEY="key-test", MAILGUN_DOMAIN="mg.example.test",
                 MAILGUN_API_BASE="https://api.eu.mailgun.net")
        seen = {}

        class _Opener:
            def open(self, req, timeout=None):
                seen["url"] = req.full_url
                class _R:
                    status = 200
                    def __enter__(s):
                        return s
                    def __exit__(s, *a):
                        return False
                return _R()

        with mock.patch.object(mailgun.urllib.request, "build_opener", lambda *a, **k: _Opener()):
            self.assertTrue(mail.send("p@example.test", "A", "B"))
        self.assertEqual(seen["url"], "https://api.eu.mailgun.net/v3/mg.example.test/messages")

    def test_reply_to_is_read_through_config(self):
        self.env(MAILGUN_API_KEY="key-test", MAILGUN_DOMAIN="mg.example.test")
        seen = {}

        class _Opener:
            def open(self, req, timeout=None):
                seen["body"] = req.data.decode()
                class _R:
                    status = 200
                    def __enter__(s):
                        return s
                    def __exit__(s, *a):
                        return False
                return _R()

        with mock.patch.object(config, "_db_overrides", return_value={"MAIL_REPLY_TO": "salud@example.test"}), \
                mock.patch.object(mailgun.urllib.request, "build_opener", lambda *a, **k: _Opener()):
            config._reset_cache_para_tests()
            self.assertTrue(mail.send("p@example.test", "A", "B"))
        self.assertIn("h%3AReply-To=salud%40example.test", seen["body"])


class TestSettings(_Base):
    def _card(self):
        return next(g for g in sa._api_keys_payload() if g["id"] == "email")

    def test_one_email_card_with_masked_mailjet_keys_and_a_provider_choice(self):
        self.env(**_MJ_ENV)
        card = self._card()
        self.assertEqual(card["probar"], "email")
        campos = {c["clave"]: c for c in card["campos"]}
        for key in ("MAILJET_API_KEY", "MAILJET_API_SECRET", "MAILGUN_API_KEY"):
            self.assertEqual(campos[key]["tipo"], "secret")
            self.assertNotIn("valor", campos[key])
        self.assertEqual(campos["MAILJET_API_SECRET"]["last4"], "9f3e")
        self.assertNotIn("mj-private-test-9f3e", json.dumps(card))
        provider = campos["CONNECTOR_EMAIL_TRANSPORT"]
        self.assertEqual(provider["tipo"], "choice")
        self.assertEqual(provider["valor"], "mailjet")
        self.assertEqual([o["valor"] for o in provider["opciones"]], ["", "mailjet"])
        self.assertIn("MAIL_REPLY_TO", campos)
        self.assertIn("MAILGUN_API_BASE", campos)

    def test_provider_outside_the_list_is_refused(self):
        with mock.patch.object(config, "guardar") as guardar:
            _, err = sa._guardar_api_keys({"CONNECTOR_EMAIL_TRANSPORT": "sendgrid"}, [])
        self.assertTrue(err)
        guardar.assert_not_called()

    def test_provider_mailjet_is_saved(self):
        with mock.patch.object(config, "guardar") as guardar:
            _, err = sa._guardar_api_keys({"CONNECTOR_EMAIL_TRANSPORT": "mailjet"}, [])
        self.assertIsNone(err)
        guardar.assert_called_once()
        self.assertEqual(guardar.call_args[0][0]["CONNECTOR_EMAIL_TRANSPORT"], "mailjet")

    def test_probe_mailgun_uses_the_configured_region(self):
        self.env(MAILGUN_API_KEY="key-test", MAILGUN_DOMAIN="mg.example.test",
                 MAILGUN_API_BASE="https://api.eu.mailgun.net")
        with mock.patch.object(mail, "get_json", return_value=({"domain": {"state": "active"}}, None)) as gj:
            r = sa.resolve_probe("email")
        self.assertTrue(r["ok"], r)
        self.assertEqual(gj.call_args[0][0], "https://api.eu.mailgun.net/v3/domains/mg.example.test")

    def test_probe_mailjet_checks_the_sender(self):
        self.env(**_MJ_ENV)
        senders = {"Count": 2, "Total": 2, "Data": [
            {"Email": "otro@example.test", "Status": "Active"},
            {"Email": "*@mj.salud.example.test", "Status": "Active"},
        ]}
        with mock.patch.object(mail, "get_json", return_value=(senders, None)) as gj:
            r = sa.resolve_probe("email")
        self.assertTrue(r["ok"], r)
        url = gj.call_args[0][0]
        self.assertTrue(url.startswith("https://api.mailjet.com/v3/REST/sender"), url)
        auth = gj.call_args[1]["headers"]["Authorization"]
        self.assertEqual(auth, "Basic " + base64.b64encode(b"mj-public-test:mj-private-test-9f3e").decode())

    def test_probe_mailjet_unvalidated_sender_is_not_ok(self):
        self.env(**_MJ_ENV)
        with mock.patch.object(mail, "get_json", return_value=({"Data": [
                {"Email": "turnos@mj.salud.example.test", "Status": "Inactive"}]}, None)):
            r = sa.resolve_probe("email")
        self.assertFalse(r["ok"])

    def test_probe_mailjet_without_keys_names_what_is_missing(self):
        self.env(CONNECTOR_EMAIL_TRANSPORT="mailjet")
        self.no_network()
        r = sa.resolve_probe("email")
        self.assertFalse(r["ok"])
        self.assertIn("MAILJET_API_KEY", r["detalle"])


class TestSystemStatus(_Base):
    def test_mailjet_cluster_reads_mailjet(self):
        self.env(**_MJ_ENV)
        with mock.patch.object(mail, "get_json", return_value=({"Data": [
                {"Email": "turnos@mj.salud.example.test", "Status": "Active"}]}, None)):
            c = system_status._cluster_email()
        self.assertEqual(c["estado"], "ok", c)
        self.assertIn("Mailjet", json.dumps(c, ensure_ascii=False))

    def test_mailjet_without_keys_is_an_error_pointing_at_the_field(self):
        self.env(CONNECTOR_EMAIL_TRANSPORT="mailjet")
        c = system_status._cluster_email()
        self.assertEqual(c["estado"], "error")
        self.assertIn("MAILJET_API_KEY", [i.get("campo") for i in c["items"]])


if __name__ == "__main__":
    unittest.main()
