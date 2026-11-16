"""Chat con IA y lector de órdenes sin clave de IA, y detrás de una IP compartida.

salud.pinamar.gob.ar (2026-10-28, informe de la contraparte técnica): sin OPENROUTER_API_KEY el chat
abría y contestaba «El asistente no está disponible», y «Subí tu orden» terminaba
en «Hiciste varias consultas seguidas» aunque nada llamara a la IA — el límite
contaba cada intento SIN clave, y por IP, y toda la población del hospital llega
por la misma IP (la puerta municipal). Acá se fija:

  - sin clave (o TURNOS_AI_DISABLED=1) la IA no está configurada: no hay lanzador
    del chat, /api/ai-status dice que no, y el lector NO cuenta intentos — la orden
    se guarda («pendiente_ia») y el paciente sigue;
  - con clave, los límites cuentan por NAVEGADOR (`ip|X-Turnos-Client`), con un
    techo por IP contra quien rota ids; sin encabezado vale el cupo estricto por IP;
  - lo mismo para el límite horario del chat.
"""
import base64
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _env  # noqa: E402

_env.process_defaults(TURNOS_SKIP_HSI="1")

from app.ai import abuse_guard, ai_client, ai_cooldown, ai_costs, ai_health  # noqa: E402
from app.core import jinja_env, rate_window  # noqa: E402
from app.modules.chat import chat_api  # noqa: E402
from app.modules.turnos import booking_referrals  # noqa: E402
from app.modules.turnos import booking_requests  # noqa: E402

_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
_BROWSER_A = "A" * 22
_BROWSER_B = "B" * 22


def _clear_counters():
    abuse_guard._HITS.clear()
    abuse_guard._IP_HITS.clear()
    ai_costs._WINDOW.clear()
    ai_cooldown._reset_para_tests()
    chat_api._HITS.clear()


class _NoKey(unittest.TestCase):
    """An installation with no AI key at all (salud before 2026-10-28)."""

    def setUp(self):
        _env.set_env(self, TURNOS_FAKE_AI=None, TURNOS_AI_DISABLED=None, TURNOS_AI_FORCE=None,
                     TURNOS_AI_PER_HOUR="3", TURNOS_AI_PER_DAY="30", TURNOS_IMG_BURST="50")
        patcher = mock.patch.object(ai_client, "available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        _clear_counters()
        self.addCleanup(_clear_counters)


class _WithKey(unittest.TestCase):
    """An installation with a key (the deterministic stand-in answers)."""

    def setUp(self):
        _env.set_env(self, TURNOS_FAKE_AI="1", TURNOS_AI_DISABLED=None, TURNOS_AI_FORCE=None,
                     TURNOS_AI_PER_HOUR="2", TURNOS_AI_PER_DAY="30", TURNOS_IMG_BURST="50",
                     TURNOS_AI_IP_CEILING_PER_HOUR="5")
        _clear_counters()
        self.addCleanup(_clear_counters)


class _Analyze:
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for patcher in (mock.patch.object(booking_requests, "REFERRALS_DIR", tmp.name),
                        mock.patch.object(abuse_guard, "turnstile_ok", return_value=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def analyze(self, ip="10.0.0.1", browser=None):
        headers = {abuse_guard.CLIENT_HEADER: browser} if browser else {}
        body = {"servicio_id": "cardiologia", "especialidad": "Cardiología",
                "orden": {"filename": "orden.png", "mime": "image/png", "data_b64": _PNG_B64}}
        return booking_referrals._analyze_referral_core(headers, ip, ip, lambda: body)


class TestHealthWithoutKey(_NoKey):
    def test_not_configured_without_key(self):
        self.assertFalse(ai_health.configured())
        self.assertFalse(ai_health.available())
        status = ai_health.status()
        self.assertFalse(status["disponible"])
        self.assertEqual(status["motivo"], "sin_clave")

    def test_disabled_with_a_key_is_not_configured(self):
        _env.set_env(self, TURNOS_AI_DISABLED="1")
        with mock.patch.object(ai_client, "available", return_value=False):
            self.assertFalse(ai_health.configured())

    def test_fake_ai_needs_no_key(self):
        _env.set_env(self, TURNOS_FAKE_AI="1")
        self.assertTrue(ai_health.configured())
        self.assertTrue(ai_health.available())

    def test_force_up_still_wins(self):
        _env.set_env(self, TURNOS_AI_FORCE="up")
        self.assertTrue(ai_health.available())

    def test_a_key_makes_it_configured(self):
        with mock.patch.object(ai_client, "available", return_value=True):
            self.assertTrue(ai_health.configured())


class TestReaderWithoutKey(_Analyze, _NoKey):
    def test_attempts_without_a_key_are_never_limited(self):
        # 3 per hour is the limit set above: 12 attempts from one IP, all saved.
        for _ in range(12):
            status, out = self.analyze()
            self.assertEqual(status, 200, out)
            self.assertFalse(out["ia_disponible"])
            self.assertEqual(out["ai"]["verdict"], "pendiente_ia")
            self.assertTrue(out["token"])

    def test_the_ip_ceiling_still_bounds_a_script(self):
        _env.set_env(self, TURNOS_AI_IP_CEILING_PER_HOUR="4")
        codes = [self.analyze(browser=f"{n:02d}" + "x" * 20)[0] for n in range(6)]
        self.assertEqual(codes, [200, 200, 200, 200, 429, 429])

    def test_provider_down_with_a_key_counts_nothing_either(self):
        # a key exists, the breaker is open: nothing is spent, nobody is told to wait.
        with mock.patch.object(ai_client, "available", return_value=True), \
                mock.patch.object(ai_health, "available", return_value=False):
            for _ in range(8):
                status, out = self.analyze()
                self.assertEqual(status, 200, out)


class TestReaderWithKeyPerBrowser(_Analyze, _WithKey):
    def test_two_browsers_behind_one_ip_have_their_own_quota(self):
        a = [self.analyze(browser=_BROWSER_A)[0] for _ in range(3)]
        self.assertEqual(a, [200, 200, 429])  # 2 per hour per browser
        b = [self.analyze(browser=_BROWSER_B)[0] for _ in range(2)]
        self.assertEqual(b, [200, 200])       # B is not locked out by A

    def test_the_limit_answer_carries_the_next_step_not_a_dead_end(self):
        # remedies rule: a refusal says what to do next (29.10, the reference-deployment counterpart on salud).
        for _ in range(2):
            self.analyze(browser=_BROWSER_A)
        status, out = self.analyze(browser=_BROWSER_A)
        self.assertEqual((status, out["error"]), (429, "limite"))
        self.assertIn("Servicios y Centros", out["mensaje"])
        self.assertIn("centro de salud", out["mensaje"])
        self.assertNotIn("Esperá un rato", out["mensaje"])

    def test_without_the_header_the_strict_per_ip_bucket_stays(self):
        self.assertEqual([self.analyze()[0] for _ in range(3)], [200, 200, 429])

    def test_rotating_ids_hits_the_ip_ceiling(self):
        codes = [self.analyze(browser=f"{n:02d}" + "z" * 20)[0] for n in range(7)]
        self.assertEqual(codes, [200] * 5 + [429] * 2)

    def test_a_zero_ceiling_is_off_not_everything_refused(self):
        # 0 = no limit, like every other limit of the installation (29.10, the maintainer's agent).
        _env.set_env(self, TURNOS_AI_IP_CEILING_PER_HOUR="0", TURNOS_AI_IP_CEILING_PER_DAY="0",
                     TURNOS_AI_PER_HOUR="50")
        self.assertEqual(abuse_guard._ceiling_per_hour(), 0)
        codes = [self.analyze(browser=f"{n:02d}" + "o" * 20)[0] for n in range(8)]
        self.assertEqual(codes, [200] * 8)
        self.assertEqual(abuse_guard._IP_HITS, {})  # nothing is recorded when both are off

    def test_one_ceiling_off_keeps_the_other(self):
        _env.set_env(self, TURNOS_AI_IP_CEILING_PER_HOUR="0", TURNOS_AI_IP_CEILING_PER_DAY="3",
                     TURNOS_AI_PER_HOUR="50")
        self.assertEqual([abuse_guard.ceiling_ok("10.9.9.9") for _ in range(5)],
                         [True, True, True, False, False])
        _clear_counters()
        _env.set_env(self, TURNOS_AI_IP_CEILING_PER_HOUR="2", TURNOS_AI_IP_CEILING_PER_DAY="0")
        self.assertEqual([abuse_guard.ceiling_ok("10.9.9.9") for _ in range(4)],
                         [True, True, False, False])

    def test_malformed_id_falls_back_to_the_ip(self):
        self.assertEqual(abuse_guard.identity("1.2.3.4", {abuse_guard.CLIENT_HEADER: "short"}), "1.2.3.4")
        self.assertEqual(abuse_guard.identity("1.2.3.4", {abuse_guard.CLIENT_HEADER: "a b" * 8}), "1.2.3.4")
        self.assertEqual(abuse_guard.identity("1.2.3.4", {abuse_guard.CLIENT_HEADER: _BROWSER_A}),
                         "1.2.3.4|" + _BROWSER_A)

    def test_junk_from_one_browser_does_not_block_another(self):
        for _ in range(12):
            ai_costs._WINDOW.setdefault("10.0.0.1|" + _BROWSER_A, []).append((time.time(), False))
        self.assertTrue(ai_costs.blocked("10.0.0.1|" + _BROWSER_A))
        self.assertFalse(ai_costs.blocked("10.0.0.1|" + _BROWSER_B))
        self.assertEqual(self.analyze(browser=_BROWSER_A)[0], 429)
        self.assertEqual(self.analyze(browser=_BROWSER_B)[0], 200)

    def test_cooldown_is_per_browser(self):
        _env.set_env(self, TURNOS_IMG_BURST="1", TURNOS_AI_PER_HOUR="20")
        self.assertEqual(self.analyze(browser=_BROWSER_A)[0], 200)
        status, out = self.analyze(browser=_BROWSER_A)
        self.assertEqual((status, out["error"]), (429, "enfriamiento"))
        self.assertEqual(self.analyze(browser=_BROWSER_B)[0], 200)


class TestChatLimitPerBrowser(_WithKey):
    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_CHAT_PER_HOUR="2", TURNOS_CHAT_IP_CEILING_PER_HOUR="5")

    def test_two_browsers_behind_one_ip_do_not_share_the_hour(self):
        a = [chat_api._within_limits("10.0.0.2", {abuse_guard.CLIENT_HEADER: _BROWSER_A}) for _ in range(3)]
        self.assertEqual(a, [True, True, False])
        self.assertTrue(chat_api._within_limits("10.0.0.2", {abuse_guard.CLIENT_HEADER: _BROWSER_B}))

    def test_no_header_is_the_strict_per_ip_bucket(self):
        self.assertEqual([chat_api._within_limits("10.0.0.3", None) for _ in range(3)], [True, True, False])

    def test_rotating_ids_hits_the_ip_ceiling(self):
        ok = [chat_api._within_limits("10.0.0.4", {abuse_guard.CLIENT_HEADER: f"{n:02d}" + "c" * 20})
              for n in range(7)]
        self.assertEqual(ok, [True] * 5 + [False] * 2)

    def test_a_zero_chat_ceiling_is_off(self):
        _env.set_env(self, TURNOS_CHAT_PER_HOUR="50", TURNOS_CHAT_IP_CEILING_PER_HOUR="0")
        ok = [chat_api._within_limits("10.0.0.6", {abuse_guard.CLIENT_HEADER: f"{n:02d}" + "d" * 20})
              for n in range(8)]
        self.assertEqual(ok, [True] * 8)

    def test_compute_output_answers_429_with_the_remedy_text(self):
        turn = chat_api.ChatTurnInput(None, "10.0.0.5", body={"mensaje": "hola"},
                                      headers={abuse_guard.CLIENT_HEADER: _BROWSER_A})
        with mock.patch.object(rate_window, "allow", return_value=False):
            status, out = chat_api.compute_output(turn)
        self.assertEqual((status, out["error"]), (429, "limite"))


class TestChatLauncher(unittest.TestCase):
    """The chat entrance is painted only when there is an AI to talk to."""

    def _bootstrap(self):
        return jinja_env.ENV.get_template("_chat_bootstrap.html").render()

    def test_no_launcher_without_a_key(self):
        _env.set_env(self, TURNOS_FAKE_AI=None, TURNOS_AI_DISABLED=None)
        with mock.patch.object(ai_client, "available", return_value=False):
            html = self._bootstrap()
        self.assertNotIn("stz-chat-launcher", html)
        self.assertNotIn("chat-boot-error", html)

    def test_no_launcher_when_disabled(self):
        _env.set_env(self, TURNOS_FAKE_AI=None, TURNOS_AI_DISABLED="1")
        self.assertNotIn("stz-chat-launcher", self._bootstrap())

    def test_launcher_with_a_key(self):
        _env.set_env(self, TURNOS_FAKE_AI=None, TURNOS_AI_DISABLED=None)
        with mock.patch.object(ai_client, "available", return_value=True):
            html = self._bootstrap()
        self.assertIn('id="stz-chat-launcher"', html)

    def test_launcher_with_the_stand_in(self):
        _env.set_env(self, TURNOS_FAKE_AI="1")
        self.assertIn('id="stz-chat-launcher"', self._bootstrap())


class TestOutageIsARemedy(unittest.TestCase):
    def test_text_names_both_destinations_and_no_dead_end(self):
        from app.modules.chat import chat_agent
        texto, enlaces, _ = chat_agent._outage("es", [], False)
        self.assertIn("Por ahora no podemos responderte por chat", texto)
        self.assertNotIn("no está disponible", texto)
        self.assertEqual({e["href"] for e in enlaces}, {"/", "/centers"})
        for e in enlaces:
            self.assertIn(e["label"], texto)

    def test_every_language_carries_both_links(self):
        import json
        for lang in ("es", "en", "pt", "fr", "it", "de", "ru", "uk", "ja", "zh"):
            with open(os.path.join(ROOT, "locales", lang, "assistant.json"), encoding="utf-8") as f:
                text = json.load(f)["assistant.unavailable"]
            with self.subTest(lang=lang):
                self.assertIn("{services}", text)
                self.assertIn("{centers}", text)


if __name__ == "__main__":
    unittest.main()
