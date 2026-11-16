"""test_ai_health_wiring.py — el breaker compartido (ai_health.py) cableado
en TODAS las superficies de IA, no sólo ai_referral.py.

Auditoría 2026-08-06: cuando OpenRouter se queda sin crédito (402) o falla,
ai_health.available() sólo lo consultaba ai_referral.py. chat_agent,
interview_agent y chart_summary seguían intentando la red y podían mostrar
un error crudo en vez de degradar con gracia como ya hacía la orden médica.
Este archivo fija:
  1. Las tres superficies restantes chequean ai_health.available() ANTES de
     llamar a OpenRouter (mismo camino que "sin OPENROUTER_API_KEY") y
     reportan el resultado de la llamada hacia ai_health (para que el
     breaker realmente se abra/cierre).
  2. interview_agent y ai_referral respetan un tope de llamadas concurrentes
     (semáforo no bloqueante) sin que agotarlo abra el breaker compartido
     (es autolimitación, no una caída del servicio).
  3. Una orden médica sigue llegando a la cola de revisión manual
     (ai_json verdict='pendiente_ia', nunca bloquea la reserva) cuando
     OpenRouter devuelve 402 — incluso con el semáforo nuevo en el medio.

Correr:  TURNOS_FAKE_AI=1 python3 -W error::ResourceWarning -m unittest discover -s tests -p "test_ai_health_wiring.py"
"""

import io
import os
import sys
import tempfile
import unittest
import urllib.error
from datetime import date
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_set(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.ai import ai_health  # noqa: E402
from app.ai import ai_referral  # noqa: E402
from app.core import db_booking  # noqa: E402
from app.core import db_accounts  # noqa: E402
from app.core import db_clinical  # noqa: E402
from app.modules.chat import chat_agent        # noqa: E402
from app.ai import chart_summary  # noqa: E402
from app.modules.intake import interview_agent  # noqa: E402

import _dbtest  # noqa: E402

_db = _dbtest.PerModuleDb("sana_turnos_test_aihealthwiring",
                          seed=lambda: db_accounts.get_user("__forzar_esquema__"))


def setUpModule():
    global HAVE_DB, DB_SKIP_REASON, _TEST_URL
    _db.set_up()
    HAVE_DB, DB_SKIP_REASON = _db.have_db, _db.skip_reason
    _TEST_URL = _db.test_url


def tearDownModule():
    _db.tear_down()


def _http_error(testcase, code):
    """Same shape as test_ai_health.py's own helper: `-W error::ResourceWarning`
    turns an unclosed HTTPError.fp into a hard failure, so every instance gets
    closed on the calling test's teardown."""
    err = urllib.error.HTTPError("http://x", code, "msg", {}, io.BytesIO(b""))
    testcase.addCleanup(err.close)
    return err


def _reset_health():
    ai_health._STATE.update({"disponible": True, "desde": None, "motivo": None})


# ---- chat_agent -------------------------------------------------------------

class ChatAgentBreakerTestCase(unittest.TestCase):
    def setUp(self):
        _reset_health()
        self.addCleanup(_reset_health)
        _env.set_env(self, TURNOS_AI_FORCE=None)

    def test_breaker_abierto_no_llama_a_openrouter(self):
        _env.set_env(self, TURNOS_AI_FORCE="down")
        called = []
        with mock.patch.object(chat_agent, "_save"), \
             mock.patch.object(chat_agent, "_load_history", return_value=[]), \
             mock.patch.object(chat_agent, "_first_user_message", return_value=""), \
             mock.patch.object(chat_agent, "_turns_used", return_value=1), \
             mock.patch.object(chat_agent, "_env", side_effect=lambda k, d="": {
                 "TURNOS_FAKE_CHAT_AI": "0", "OPENROUTER_API_KEY": "test-key",
             }.get(k, d)), \
             mock.patch("app.ai.ai_client.primary_key", return_value="test-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=lambda *a, **k: called.append(1)):
            out = chat_agent._run_turn(None, "hola", "test:breaker-down", "web")
        self.assertEqual(called, [], "el breaker abierto no debe llegar a la red")
        self.assertIn("Por ahora no podemos responderte por chat", out["texto"])

    def test_falla_de_servicio_abre_el_breaker_compartido(self):
        with mock.patch.object(chat_agent, "_save"), \
             mock.patch.object(chat_agent, "_load_history", return_value=[]), \
             mock.patch.object(chat_agent, "_first_user_message", return_value=""), \
             mock.patch.object(chat_agent, "_turns_used", return_value=1), \
             mock.patch.object(chat_agent, "_env", side_effect=lambda k, d="": {
                 "TURNOS_FAKE_CHAT_AI": "0", "OPENROUTER_API_KEY": "test-key",
             }.get(k, d)), \
             mock.patch("app.ai.ai_client.primary_key", return_value="test-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_http_error(self, 402)):
            out = chat_agent._run_turn(None, "hola", "test:breaker-402", "web")
        self.assertIn("Por ahora no podemos responderte por chat", out["texto"])
        self.assertFalse(ai_health.available(),
                          "un 402 en el chat también tiene que abrir el breaker compartido")

    def test_exito_reporta_ok_y_no_rompe_nada(self):
        with mock.patch.object(chat_agent, "_save"), \
             mock.patch.object(chat_agent, "_load_history", return_value=[]), \
             mock.patch.object(chat_agent, "_first_user_message", return_value=""), \
             mock.patch.object(chat_agent, "_turns_used", return_value=1), \
             mock.patch.object(chat_agent, "_env", side_effect=lambda k, d="": {
                 "TURNOS_FAKE_CHAT_AI": "0", "OPENROUTER_API_KEY": "test-key",
             }.get(k, d)), \
             mock.patch("app.ai.ai_client.primary_key", return_value="test-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", return_value={
                 "choices": [{"message": {"content": "Hola, ¿en qué te ayudo?"}}]}):
            out = chat_agent._run_turn(None, "hola", "test:breaker-ok", "web")
        self.assertIn("Hola", out["texto"])
        self.assertTrue(ai_health.available())


# ---- interview_agent ---------------------------------------------------------

class InterviewAgentBreakerTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_health()
        self.addCleanup(_reset_health)
        _env.set_env(self, TURNOS_AI_FORCE=None)

    def _interview(self):
        return db_clinical.interview_create(entry_mode="post_booking", matched=True)

    def test_breaker_abierto_no_llama_a_openrouter(self):
        _env.set_env(self, TURNOS_AI_FORCE="down")
        called = []
        interview = self._interview()
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=lambda *a, **k: called.append(1)):
            resultado = interview_agent.run_turn(interview, "me duele la cabeza")
        self.assertEqual(called, [])
        self.assertEqual(resultado["texto"], interview_agent._DISCULPA)
        self.assertFalse(resultado["fin"])

    def test_falla_de_servicio_abre_el_breaker_compartido(self):
        interview = self._interview()
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_http_error(self, 402)):
            resultado = interview_agent.run_turn(interview, "me duele la cabeza")
        self.assertEqual(resultado["texto"], interview_agent._DISCULPA)
        self.assertFalse(ai_health.available())

    def test_semaforo_lleno_degrada_sin_abrir_el_breaker(self):
        # Agota el semáforo propio de la entrevista: la llamada real nunca
        # se hace, pero eso es autolimitación -- no debe reportarse como
        # caída del servicio al breaker compartido.
        interview = self._interview()
        acquired = 0
        try:
            while interview_agent._MODEL_SLOTS.acquire(blocking=False):
                acquired += 1
            with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
                 mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
                 mock.patch("app.ai.ai_client.available", return_value=True):
                resultado = interview_agent.run_turn(interview, "me duele la cabeza")
        finally:
            for _ in range(acquired):
                interview_agent._MODEL_SLOTS.release()
        self.assertEqual(resultado["texto"], interview_agent._DISCULPA)
        self.assertTrue(ai_health.available())


# ---- chart_summary ------------------------------------------------------------

class ChartSummaryBreakerTestCase(unittest.TestCase):
    def setUp(self):
        _reset_health()
        self.addCleanup(_reset_health)
        _env.set_env(self, TURNOS_AI_FORCE=None)

    def test_breaker_abierto_no_llama_a_openrouter_y_degrada_en_silencio(self):
        _env.set_env(self, TURNOS_AI_FORCE="down")
        called = []
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=lambda *a, **k: called.append(1)):
            fila = chart_summary.generate("30111222", "medico-inexistente-breaker",
                                          force=True)
        self.assertEqual(called, [])
        self.assertIsNotNone(fila)
        self.assertEqual(fila["modelo"], "ai_no_disponible")

    def test_falla_de_servicio_degrada_y_abre_el_breaker(self):
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_http_error(self, 402)):
            fila = chart_summary.generate("30111223", "medico-inexistente-402",
                                          force=True)
        self.assertIsNotNone(fila)
        self.assertEqual(fila["modelo"], "fallback")  # el resumen determinístico igual sale
        self.assertFalse(ai_health.available())


# ---- ai_referral: el semáforo no abre el breaker por sí solo -----------------

class ReferralSemaphoreTestCase(unittest.TestCase):
    def setUp(self):
        _reset_health()
        self.addCleanup(_reset_health)
        _env.set_env(self, TURNOS_AI_FORCE=None)

    def test_semaforo_lleno_no_abre_el_breaker(self):
        sem = ai_referral._MODEL_SLOTS
        acquired = 0
        try:
            while sem.acquire(blocking=False):
                acquired += 1
            with self.assertRaises(RuntimeError):
                ai_referral._call_openrouter([{"type": "text", "text": "x"}],
                                             "modelo-x", "clave-x")
        finally:
            for _ in range(acquired):
                sem.release()
        self.assertTrue(ai_health.available())


# ---- una orden médica no bloquea la reserva cuando OpenRouter da 402 --------

class OrdenAterrizaEnColaTestCase(unittest.TestCase):
    def setUp(self):
        if not HAVE_DB:
            self.skipTest(DB_SKIP_REASON or "sin base de datos")
        _reset_health()
        self.addCleanup(_reset_health)
        _env.set_env(self, TURNOS_AI_FORCE=None)
        import psycopg
        with psycopg.connect(_TEST_URL, autocommit=True, connect_timeout=5) as c:
            with c.cursor() as cur:
                cur.execute("TRUNCATE booking_requests RESTART IDENTITY CASCADE")
        self.tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        self.tmp.write(b"\x89PNG\r\n")
        self.tmp.close()
        self.addCleanup(lambda: os.path.exists(self.tmp.name) and os.unlink(self.tmp.name))

    def test_402_en_openrouter_deja_la_orden_pendiente_de_revision_manual(self):
        with _env.patched_env({"TURNOS_FAKE_AI": ""}), \
             mock.patch("app.ai.ai_client.primary_key", return_value="fake-key"), \
             mock.patch("app.ai.ai_client.available", return_value=True), \
             mock.patch("app.ai.ai_client.call", side_effect=_http_error(self, 402)):
            ai_json = ai_referral.analyze_referral_3pass(
                self.tmp.name, "image/png",
                {"dni": "30111222", "nombre": "Ana", "apellido": "Lopez",
                 "servicio_id": "svc-x"})
        self.assertEqual(ai_json["verdict"], "pendiente_ia")
        self.assertFalse(ai_health.available())

        sid = db_booking.create_booking_request(
            "30111222", "Ana", "Lopez", date(1990, 1, 1),
            "wa:99", "svc-x", "centro-x", "image/png",
            solicitante_dni="30111222")
        db_booking.set_referral(sid, self.tmp.name, "image/png")
        db_booking.save_ai(sid, ai_json)

        fila = db_booking.get_booking_request(sid)
        self.assertEqual(fila["ai_json"]["verdict"], "pendiente_ia")
        self.assertEqual(db_booking.count_pending_ai(), 1)


if __name__ == "__main__":
    unittest.main()
