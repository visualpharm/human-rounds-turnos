"""test_meta_wa_limits.py — Meta WhatsApp: the pause after a rate limit, the cause of an error in words, and the
verification code as the approved Authentication template (otp_send.py, 2026-11-16).

1. A rate limit (codes 4, 80007, 130429, 131048, 131056, HTTP 429, any Retry-After) pauses EVERY Meta send for
   Retry-After seconds — 60 s without the header, never more than 15 min — and nothing reaches the network
   while it lasts. The pause lives on the data volume, so another process (the queue worker) sees it too.
2. An error code becomes its cause in words in the log (never Meta's own message) and in «Sistema».
3. OTP_PROVIDER=meta sends the code as the template named by META_WA_OTP_TEMPLATE, with the copy-code button;
   without the variable it stays a logged no-op; a refused template is a False, never a raise.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr
from email.message import Message
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _clock  # noqa: E402
import _env  # noqa: E402
_env.process_defaults(DATABASE_URL="", TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import config  # noqa: E402
config.ENV_PATH = ""  # never read the checkout's .env

from app.notifications import otp_send  # noqa: E402
from app.ops import demo_installation  # noqa: E402

_DESTINO = "5492255111222"
_TEL_LOCAL = "2255111222"
_ECO = "Message failed: recipient 5492255111222 said Juan Perez"


def _http_error(status, code=None, retry_after=None, subcode=None):
    body = {"error": {"message": _ECO}}
    if code is not None:
        body["error"]["code"] = code
    if subcode is not None:
        body["error"]["error_subcode"] = subcode
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return urllib.error.HTTPError("https://graph.example/x", status, "err", headers,
                                  io.BytesIO(json.dumps(body).encode()))


class _Ok:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b'{"messages": [{"id": "wamid.1"}]}'


class _Base(unittest.TestCase):
    def setUp(self):
        self.datos = tempfile.mkdtemp(prefix="meta_wa_")
        self.addCleanup(shutil.rmtree, self.datos, ignore_errors=True)
        _env.set_env(self, TURNOS_DATA_DIR=self.datos, META_WA_TOKEN="tok", META_WA_PHONE_NUMBER_ID="123",
                     META_WA_GRAPH_VERSION="v25.0", TURNOS_WA_PROVIDER="meta", OTP_PROVIDER=None,
                     META_WA_OTP_TEMPLATE=None, TURNOS_WA_NOTIFY=None, TURNOS_WA_TEST_RECIPIENTS=None)
        otp_send._estado_memoria.clear()
        self.addCleanup(otp_send._estado_memoria.clear)
        self.clock = _clock.freeze(self, "2026-11-16T04:00:00Z")
        patcher = mock.patch.object(demo_installation, "enabled", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _send_failing(self, error, sender=None):
        """One send that Meta answers with `error`; returns (result, log)."""
        self.addCleanup(error.close)
        salida = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=error), redirect_stderr(salida):
            ok = (sender or (lambda: otp_send._enviar_meta(_DESTINO, "hola")))()
        return ok, salida.getvalue()

    def _assert_blocked(self):
        """While paused, a send never reaches urlopen."""
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("urlopen during a pause")), \
                redirect_stderr(io.StringIO()):
            self.assertFalse(otp_send._enviar_meta(_DESTINO, "hola"))
            self.assertFalse(otp_send._enviar_meta_plantilla(_DESTINO, "turnos_recordatorio", "es_AR", ["x"]))


class TestRateLimitPause(_Base):
    def test_every_rate_limit_code_pauses_all_meta_sends(self):
        for code in (130429, 131048, 131056, 80007, 4):
            with self.subTest(code=code):
                otp_send.meta_pausa_reset()
                ok, log = self._send_failing(_http_error(400, code))
                self.assertFalse(ok)
                self.assertEqual(otp_send.meta_pausa_restante(), 60)
                self.assertEqual(otp_send.meta_estado()["pausa_codigo"], code)
                self.assertIn("pausa", log)
                self._assert_blocked()

    def test_http_429_without_a_code_pauses(self):
        ok, _log = self._send_failing(_http_error(429))
        self.assertFalse(ok)
        self.assertEqual(otp_send.meta_pausa_restante(), 60)
        self.assertEqual(otp_send.meta_estado()["pausa_codigo"], 429)
        self._assert_blocked()

    def test_retry_after_sets_the_length(self):
        self._send_failing(_http_error(429, 130429, retry_after=120))
        self.assertEqual(otp_send.meta_pausa_restante(), 120)

    def test_retry_after_as_an_http_date(self):
        self._send_failing(_http_error(429, retry_after="Mon, 16 Nov 2026 04:05:00 GMT"))
        self.assertEqual(otp_send.meta_pausa_restante(), 300)

    def test_retry_after_alone_pauses_even_without_a_rate_code(self):
        self._send_failing(_http_error(503, retry_after=30))
        self.assertEqual(otp_send.meta_pausa_restante(), 30)

    def test_no_retry_after_means_sixty_seconds(self):
        self._send_failing(_http_error(400, 130429))
        self.assertEqual(otp_send.meta_pausa_restante(), otp_send._PAUSA_SIN_RETRY_AFTER_S)
        self.assertEqual(otp_send._PAUSA_SIN_RETRY_AFTER_S, 60)

    def test_the_pause_is_capped_at_fifteen_minutes(self):
        self._send_failing(_http_error(429, 130429, retry_after=7200))
        self.assertEqual(otp_send.meta_pausa_restante(), 15 * 60)

    def test_the_pause_ends_and_sending_resumes(self):
        self._send_failing(_http_error(429, 130429, retry_after=90))
        self._assert_blocked()
        self.clock.advance(seconds=91)
        self.assertEqual(otp_send.meta_pausa_restante(), 0)
        salida = io.StringIO()
        with mock.patch("urllib.request.urlopen", return_value=_Ok()), redirect_stderr(salida):
            self.assertTrue(otp_send._enviar_meta(_DESTINO, "hola"))
        self.assertIn("terminó la pausa", salida.getvalue())
        self.assertTrue(otp_send.meta_estado()["pausa_fin_avisada"])

    def test_an_infinite_or_bogus_retry_after_falls_back_to_sixty(self):
        for valor in ("inf", "Infinity", "1e400", "nan", "-5", "soon"):
            with self.subTest(retry_after=valor):
                otp_send.meta_pausa_reset()
                ok, _log = self._send_failing(_http_error(429, 130429, retry_after=valor))
                self.assertFalse(ok)
                self.assertEqual(otp_send.meta_pausa_restante(), 60)

    def test_a_send_held_by_the_pause_is_not_a_transport_failure(self):
        # The launch digest counts what Meta answered for; a send we held back never reached Meta.
        self._send_failing(_http_error(429, 130429))
        with mock.patch.object(otp_send, "_signal_whatsapp") as senal:
            self._assert_blocked()
        senal.assert_not_called()

    def test_a_second_limit_never_shortens_a_running_pause(self):
        self._send_failing(_http_error(429, 130429, retry_after=600))
        otp_send._abrir_pausa(60, 131056)
        self.assertEqual(otp_send.meta_pausa_restante(), 600)

    def test_another_process_sees_the_pause(self):
        # The queue worker is another process: what it reads is the file, not this process's memory.
        self._send_failing(_http_error(429, 130429, retry_after=200))
        otp_send._estado_memoria.clear()
        with open(otp_send._estado_path(), encoding="utf-8") as f:
            self.assertGreater(json.load(f)["pausa_hasta"], 0)
        self.assertEqual(otp_send.meta_pausa_restante(), 200)

    def test_the_state_file_holds_no_phone_and_no_meta_text(self):
        self._send_failing(_http_error(400, 131026))
        with open(otp_send._estado_path(), encoding="utf-8") as f:
            crudo = f.read()
        self.assertNotIn(_TEL_LOCAL, crudo)
        self.assertNotIn("Juan", crudo)

    def test_an_ordinary_error_does_not_pause(self):
        self._send_failing(_http_error(400, 131047))
        self.assertEqual(otp_send.meta_pausa_restante(), 0)

    def test_an_unwritable_volume_never_breaks_the_send(self):
        _env.set_env(self, TURNOS_DATA_DIR=os.path.join(self.datos, "file"))
        with open(os.path.join(self.datos, "file"), "w", encoding="utf-8") as f:
            f.write("not a directory")
        ok, _log = self._send_failing(_http_error(429, 130429))
        self.assertFalse(ok)
        # The pause still holds in this process's memory.
        self.assertEqual(otp_send.meta_pausa_restante(), 60)


class TestErrorReasons(_Base):
    def test_known_codes_read_as_words(self):
        self.assertIn("24 horas", otp_send.meta_error_reason(131047, "es"))
        self.assertIn("idioma", otp_send.meta_error_reason(132001, "es"))
        self.assertIn("token", otp_send.meta_error_reason(190, "es"))

    def test_an_unknown_code_gets_the_generic_cause(self):
        self.assertEqual(otp_send.meta_error_reason(999999, "es"), "Error de Meta sin causa conocida")
        self.assertEqual(otp_send.meta_error_reason(None, "es"), "Error de Meta sin causa conocida")
        self.assertEqual(otp_send.meta_error_reason("abc", "es"), "Error de Meta sin causa conocida")

    def test_english_comes_from_the_catalog(self):
        self.assertIn("24 hours", otp_send.meta_error_reason(131047, "en"))

    def test_every_listed_code_has_a_spanish_and_english_entry(self):
        catalogos = {}
        for lang in ("es", "en"):
            with open(os.path.join(ROOT, "locales", lang, "admin.system.status.json"), encoding="utf-8") as f:
                catalogos[lang] = json.load(f)
        for clave, es in list(otp_send._META_MOTIVOS.values()) + [otp_send._META_MOTIVO_DESCONOCIDO]:
            self.assertEqual(catalogos["es"].get(clave), es, clave)
            self.assertTrue(catalogos["en"].get(clave), clave)

    def test_the_log_carries_the_code_and_its_cause_never_metas_text(self):
        for code, palabra in ((131047, "24 horas"), (132001, "idioma"), (190, "token")):
            with self.subTest(code=code):
                ok, log = self._send_failing(_http_error(400, code))
                self.assertFalse(ok)
                self.assertIn(f"HTTPError (400) {code}", log)
                self.assertIn(palabra, log)
                self.assertNotIn(_ECO, log)
                self.assertNotIn(_TEL_LOCAL, log)

    def test_the_log_of_an_unknown_code_says_so(self):
        _ok, log = self._send_failing(_http_error(400, 999999))
        self.assertIn("999999", log)
        self.assertIn("sin causa conocida", log)

    def test_the_last_error_is_recorded(self):
        self._send_failing(_http_error(400, 131047, subcode=2494010))
        estado = otp_send.meta_estado()
        self.assertEqual((estado["error_codigo"], estado["error_http"]), (131047, 400))
        self.assertNotIn("2494010", json.dumps(estado))

    def test_a_text_code_is_never_kept(self):
        self._send_failing(_http_error(400, "2255111222"))
        self.assertIsNone(otp_send.meta_estado()["error_codigo"])


class TestOtpTemplate(_Base):
    def setUp(self):
        super().setUp()
        _env.set_env(self, OTP_PROVIDER="meta")

    def test_without_the_variable_nothing_leaves(self):
        salida = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network")), \
                redirect_stderr(salida):
            self.assertFalse(otp_send.enviar_otp(_DESTINO, "123456"))
        self.assertIn("META_WA_OTP_TEMPLATE", salida.getvalue())

    def test_the_code_goes_as_the_template_with_the_copy_button(self):
        _env.set_env(self, META_WA_OTP_TEMPLATE="turnos_codigo")
        enviados = []

        def _urlopen(req, timeout=None):
            enviados.append(json.loads(req.data))
            return _Ok()

        salida = io.StringIO()
        with mock.patch("urllib.request.urlopen", side_effect=_urlopen), redirect_stderr(salida):
            self.assertTrue(otp_send.enviar_otp(_DESTINO, "123456"))
        self.assertEqual(len(enviados), 1)
        cuerpo = enviados[0]
        self.assertEqual(cuerpo["type"], "template")
        self.assertEqual(cuerpo["to"], _DESTINO)
        plantilla = cuerpo["template"]
        self.assertEqual(plantilla["name"], "turnos_codigo")
        self.assertEqual(plantilla["language"], {"code": "es_AR"})
        self.assertEqual(plantilla["components"], [
            {"type": "body", "parameters": [{"type": "text", "text": "123456"}]},
            {"type": "button", "sub_type": "url", "index": "0",
             "parameters": [{"type": "text", "text": "123456"}]},
        ])
        # The code never reaches the log.
        self.assertNotIn("123456", salida.getvalue())

    def test_a_refused_template_is_false_with_its_cause(self):
        _env.set_env(self, META_WA_OTP_TEMPLATE="turnos_codigo")
        ok, log = self._send_failing(_http_error(404, 132001),
                                     sender=lambda: otp_send.enviar_otp(_DESTINO, "123456"))
        self.assertFalse(ok)
        self.assertIn("132001", log)
        self.assertIn("idioma", log)
        self.assertNotIn("123456", log)
        self.assertEqual(otp_send.meta_pausa_restante(), 0)

    def test_the_code_waits_out_a_pause_too(self):
        _env.set_env(self, META_WA_OTP_TEMPLATE="turnos_codigo")
        with redirect_stderr(io.StringIO()):
            otp_send._abrir_pausa(60, 130429)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("urlopen during a pause")), \
                redirect_stderr(io.StringIO()):
            self.assertFalse(otp_send.enviar_otp(_DESTINO, "123456"))

    def test_the_demo_still_sends_no_code(self):
        _env.set_env(self, META_WA_OTP_TEMPLATE="turnos_codigo")
        with mock.patch.object(demo_installation, "enabled", return_value=True), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
            self.assertFalse(otp_send.enviar_otp(_DESTINO, "123456"))


class TestSistema(_Base):
    def _items(self):
        from app.admin_panel import system_status
        with mock.patch.object(system_status, "_wa_display_number", return_value=None):
            cluster = system_status._cluster_whatsapp()
        return {i["label"]: i for i in cluster["items"]}

    def test_a_running_pause_shows_until_when_and_why(self):
        self._send_failing(_http_error(429, 130429, retry_after=300))
        item = self._items()["Envíos por Meta"]
        self.assertEqual(item["estado"], "warn")
        self.assertEqual(item["valor"], "En pausa hasta 2026-11-16 01:05")
        self.assertIn("130429", item["detalle"])
        self.assertIn("mensajes por segundo", item["detalle"])

    def test_an_ended_pause_shows_when_it_ended(self):
        self._send_failing(_http_error(429, 130429, retry_after=300))
        self.clock.advance(seconds=600)
        item = self._items()["Envíos por Meta"]
        self.assertEqual(item["estado"], "ok")
        self.assertEqual(item["valor"], "La pausa terminó: 2026-11-16 01:05")

    def test_no_pause_no_row(self):
        self.assertNotIn("Envíos por Meta", self._items())

    def test_the_last_error_shows_its_cause(self):
        self._send_failing(_http_error(400, 131047))
        item = self._items()["Último error de Meta"]
        self.assertEqual(item["valor"], "2026-11-16 01:00")
        self.assertEqual(item["estado"], "na")
        self.assertIn("131047", item["detalle"])
        self.assertIn("24 horas", item["detalle"])

    def test_an_expired_token_is_red_until_a_send_succeeds(self):
        self._send_failing(_http_error(401, 190))
        self.assertEqual(self._items()["Último error de Meta"]["estado"], "error")
        self.clock.advance(seconds=5)
        with mock.patch("urllib.request.urlopen", return_value=_Ok()), redirect_stderr(io.StringIO()):
            self.assertTrue(otp_send._enviar_meta(_DESTINO, "hola"))
        self.assertEqual(self._items()["Último error de Meta"]["estado"], "na")

    def test_a_refused_code_template_is_red(self):
        # A misspelled META_WA_OTP_TEMPLATE refuses every code until someone fixes it.
        self._send_failing(_http_error(404, 132001))
        self.assertEqual(self._items()["Último error de Meta"]["estado"], "error")

    def test_an_old_error_leaves_the_card(self):
        self._send_failing(_http_error(400, 131047))
        self.clock.advance(hours=25)
        self.assertNotIn("Último error de Meta", self._items())

    def test_the_code_template_row(self):
        self.assertNotIn("Plantilla del código", self._items())
        _env.set_env(self, OTP_PROVIDER="meta")
        item = self._items()["Plantilla del código"]
        self.assertEqual((item["estado"], item["campo"]), ("error", "META_WA_OTP_TEMPLATE"))
        _env.set_env(self, META_WA_OTP_TEMPLATE="turnos_codigo")
        item = self._items()["Plantilla del código"]
        self.assertEqual((item["estado"], item["valor"]), ("ok", "turnos_codigo"))

    def test_the_panel_and_the_sender_read_the_provider_alike(self):
        _env.set_env(self, OTP_PROVIDER=" Meta ", META_WA_OTP_TEMPLATE="turnos_codigo")
        self.assertEqual(otp_send.otp_provider(), "meta")
        self.assertIn("Plantilla del código", self._items())
        with mock.patch("urllib.request.urlopen", return_value=_Ok()) as red, redirect_stderr(io.StringIO()):
            self.assertTrue(otp_send.enviar_otp(_DESTINO, "123456"))
        red.assert_called_once()


if __name__ == "__main__":
    unittest.main()
