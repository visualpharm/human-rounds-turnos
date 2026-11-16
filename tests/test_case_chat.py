"""test_case_chat.py — «Contanos tu caso» as a short chat (the product owner, 2026-11-07).

With no free slot the patient answers a few questions instead of a blank box;
the desk reads a summary (what they want, urgency, key facts) and can open the
whole chat, a read that lands in the clinical access log. Pinned here without
a database: the persistence seams of the interview engine and of db_chat are
replaced by recorders. The patient path end to end is
tests/e2e/journey-j21-case-and-authorised-overbooking.spec.mjs.

Run:  python3 -m unittest discover -s tests -p test_case_chat.py
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.ai import ai_client, ai_health  # noqa: E402
from app.core import db_chat  # noqa: E402
from app.modules.intake import case_chat, interview_agent as ia  # noqa: E402

TOKEN = "A" * 32
CLAVE = "caso:" + TOKEN


class _ChatCase(unittest.TestCase):
    """The interview engine with an in-memory chat_messages."""

    def setUp(self):
        _env.set_env(self, TURNOS_FAKE_AI="1", INTAKE_CASE_MAX_QUESTIONS=None)
        self.rows = []
        self.closes = []
        self._patch(ia, "_load_history", side_effect=lambda clave: [dict(m) for m in self.rows])
        self._patch(ia, "_save", side_effect=lambda clave, msg: self.rows.append(dict(msg)))
        self._patch(ia, "_close", side_effect=self.closes.append)
        self._patch(ia, "_log", side_effect=lambda *_: None)

    def _patch(self, obj, name, **kw):
        p = mock.patch.object(obj, name, **kw)
        self.addCleanup(p.stop)
        return p.start()

    def turn(self, texto=""):
        return ia.run_turn({"entry_mode": "caso", "session_key": CLAVE}, texto)


class TestQuestions(_ChatCase):

    def test_opening_offers_taps_and_says_it_is_ai(self):
        out = self.turn()
        self.assertFalse(out["fin"])
        self.assertIn("inteligencia artificial", out["texto"])
        self.assertIn("No da consejos médicos", out["texto"])
        self.assertEqual(len(out["opciones"]), 3)
        self.assertNotIn("[opciones", out["texto"])

    def test_clarifying_questions_then_close_including_urgency(self):
        self.turn()
        q1 = self.turn("Tengo resultados para mostrarle al médico")
        self.assertEqual(q1["texto"], "¿Desde cuándo te pasa?")
        q2 = self.turn("Desde la semana pasada")
        self.assertIn("¿Para cuándo necesitás que te vean?", q2["texto"])
        self.assertEqual(q2["opciones"], ["Esta semana", "Este mes", "Puede esperar"])
        done = self.turn("Esta semana")
        self.assertTrue(done["fin"])
        self.assertIn("Enviá la solicitud", done["texto"])
        self.assertEqual(self.closes, [], "a case chat has no interview row to close")

    def test_the_cap_is_code_not_prompt(self):
        """INTAKE_CASE_MAX_QUESTIONS=1: the answer after the one clarifying
        question closes the chat, with no model call even if the model
        would keep asking."""
        _env.set_env(self, INTAKE_CASE_MAX_QUESTIONS="1")
        self.assertEqual(ia.case_max_questions(), 1)
        self.turn()
        self.assertFalse(self.turn("MARKERS:¿Algo más?")["fin"])
        done = self.turn("MARKERS:¿Una más?")
        self.assertTrue(done["fin"])
        preguntas = [m for m in self.rows if m["role"] == "assistant"]
        self.assertEqual(len(preguntas), 3, "opening + 1 question + the close")

    def test_cap_is_clamped_to_one_to_three(self):
        for raw, want in (("0", 1), ("7", 3), ("x", 3), ("2", 2)):
            _env.set_env(self, INTAKE_CASE_MAX_QUESTIONS=raw)
            self.assertEqual(ia.case_max_questions(), want, raw)

    def test_the_patient_may_stop_early(self):
        self.turn()
        self.turn("Necesito una receta")
        done = self.turn("no quiero seguir")
        self.assertTrue(done["fin"])

    def test_emergency_floor_runs_first(self):
        self.turn()
        out = self.turn("Ahora no puedo respirar estando sentado")
        self.assertTrue(out["fin"])
        self.assertRegex(out["texto"], r"guardia|107|emergencias")
        self.assertEqual(self.rows[-1]["intake_outcome"], "urgent")


class TestWithoutAi(_ChatCase):

    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_FAKE_AI=None)
        self._patch(ai_client, "available", return_value=False)
        self.call = self._patch(ai_client, "call", side_effect=AssertionError("no model call"))

    def test_start_says_unavailable_and_the_wizard_keeps_the_box(self):
        self.assertEqual(case_chat.start(), {"disponible": False})

    def test_model_gone_mid_chat_closes_instead_of_apologising(self):
        self.rows = [{"role": "assistant", "content": ia._CASO_APERTURA}]
        out = self.turn("Necesito un control")
        self.assertTrue(out["fin"])
        self.assertIn("Enviá la solicitud", out["texto"])
        self.call.assert_not_called()

    def test_summary_without_ai_is_deterministic(self):
        historial = [
            {"role": "assistant", "content": ia._CASO_APERTURA},
            {"role": "user", "content": "Tengo resultados para mostrarle al médico"},
            {"role": "assistant", "content": "¿Desde cuándo te pasa?"},
            {"role": "user", "content": "Desde el lunes"},
        ]
        resumen = case_chat.summarize(historial)
        self.assertEqual(resumen["quiere"], "Tengo resultados para mostrarle al médico")
        self.assertEqual(resumen["hechos"], ["Desde el lunes"])
        self.assertEqual(resumen["fuente"], "texto")
        self.call.assert_not_called()

    def test_summary_without_ai_says_the_timing_once(self):
        # A tapped timing option is the urgency line, not one more fact under it.
        historial = [
            {"role": "assistant", "content": ia._CASO_APERTURA},
            {"role": "user", "content": "Tengo un síntoma nuevo"},
            {"role": "assistant", "content": "¿Desde cuándo te pasa?"},
            {"role": "user", "content": "Desde el lunes, peor esta semana"},
            {"role": "assistant", "content": ia._FAKE_CASO[1]},
            {"role": "user", "content": "Esta semana"},
        ]
        resumen = case_chat.summarize(historial)
        self.assertEqual(resumen["urgencia"], "alta")
        self.assertEqual(resumen["hechos"], ["Desde el lunes, peor esta semana"])


def _historial(*respuestas, urgent=False):
    out = [{"role": "assistant", "content": ia._CASO_APERTURA}]
    for r in respuestas:
        out.append({"role": "user", "content": r})
        out.append({"role": "assistant", "content": "¿Algo más?"})
    if urgent:
        out[-1] = {"role": "assistant", "content": "Andá a la guardia. [fin]", "intake_outcome": "urgent"}
    return out


class TestUrgencyInTheSummary(unittest.TestCase):

    def setUp(self):
        _env.set_env(self, TURNOS_FAKE_AI="1")

    def test_the_patients_timing_answer(self):
        for respuesta, nivel in (("Esta semana", "alta"), ("Este mes", "media"),
                                 ("Puede esperar", "baja"), ("Desde el lunes", None)):
            resumen = case_chat.summarize(_historial("Necesito un control", respuesta))
            self.assertEqual(resumen["urgencia"], nivel, respuesta)

    def test_an_emergency_anywhere_reads_emergencia(self):
        self.assertEqual(case_chat.summarize(_historial("x", urgent=True))["urgencia"], "emergencia")
        self.assertEqual(case_chat.summarize(_historial("Ahora no puedo respirar estando sentado"))["urgencia"], "emergencia")

    def test_ai_summary_is_held_to_its_shape_and_never_lowers_an_emergency(self):
        from app.ai import prompts
        _env.set_env(self, TURNOS_FAKE_AI=None)
        with mock.patch.object(prompts, "_overrides", return_value={}), \
                mock.patch.object(ai_client, "available", return_value=True), \
                mock.patch.object(ai_health, "available", return_value=True), \
                mock.patch.object(ai_health, "report"), \
                mock.patch.object(ai_client, "call", return_value={"choices": [{"message": {"content":
                    '```json\n{"quiere": "Mostrar resultados", "urgencia": "baja", "por_que": "dice que puede esperar",'
                    ' "hechos": ["análisis del lunes", ""]}\n```'}}]}) as call:
            resumen = case_chat.summarize(_historial("Tengo resultados"))
            self.assertEqual(resumen, {"quiere": "Mostrar resultados", "urgencia": "baja",
                                       "por_que": "dice que puede esperar",
                                       "hechos": ["análisis del lunes"], "fuente": "ia"})
            self.assertEqual(case_chat.summarize(_historial("x", urgent=True))["urgencia"], "emergencia")
        sent = call.call_args_list[0].args[0]["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn("Paciente: Tengo resultados", sent[1]["content"])

    def test_unusable_model_json_falls_back(self):
        self.assertIsNone(case_chat._clean_summary({"quiere": "x", "urgencia": "emergencia"}))
        self.assertIsNone(case_chat._clean_summary({"quiere": "", "urgencia": "alta"}))
        self.assertIsNone(case_chat._clean_summary(["no"]))


class TestBindIntoTheRequest(unittest.TestCase):

    def setUp(self):
        _env.set_env(self, TURNOS_FAKE_AI="1")
        self.stored = {}
        self.deleted = []
        for name, kw in (
            ("chat_metadata_get", {"return_value": None}),
            ("case_chat_set", {"side_effect": lambda rid, data: self.stored.update({rid: data})}),
            ("chat_delete_conversation", {"side_effect": lambda canal, clave: self.deleted.append((canal, clave))}),
        ):
            p = mock.patch.object(db_chat, name, **kw)
            self.addCleanup(p.stop)
            p.start()

    def test_summary_and_transcript_move_into_the_request(self):
        hist = _historial("Tengo resultados para mostrarle al médico", "Esta semana")
        with mock.patch.object(ia, "_load_history", return_value=hist):
            words = case_chat.bind(TOKEN, 41)
        self.assertEqual(words, "Tengo resultados para mostrarle al médico · Esta semana")
        data = self.stored[41]
        self.assertEqual(data["resumen"]["urgencia"], "alta")
        self.assertEqual(data["mensajes"][0]["role"], "asistente")
        self.assertEqual(data["mensajes"][0]["opciones"][0], "Tengo resultados para mostrarle al médico")
        self.assertNotIn("[opciones", data["mensajes"][0]["texto"])
        self.assertEqual(self.deleted, [("caso", CLAVE)], "the anonymous rows go")

    def test_a_stored_summary_wins(self):
        hist = _historial("Necesito un control")
        guardado = {"resumen": {"quiere": "Control", "urgencia": "media", "por_que": "", "hechos": [], "fuente": "ia"}}
        with mock.patch.object(ia, "_load_history", return_value=hist), \
                mock.patch.object(db_chat, "chat_metadata_get", return_value=guardado):
            case_chat.bind(TOKEN, 42)
        self.assertEqual(self.stored[42]["resumen"]["quiere"], "Control")

    def test_bad_or_unknown_token_binds_nothing_and_never_raises(self):
        self.assertIsNone(case_chat.bind("../x", 43))
        with mock.patch.object(ia, "_load_history", return_value=[]):
            self.assertIsNone(case_chat.bind(TOKEN, 43))
        with mock.patch.object(ia, "_load_history", side_effect=RuntimeError("db down")), \
                mock.patch.object(ia, "_log"):
            self.assertIsNone(case_chat.bind(TOKEN, 43))
        self.assertEqual(self.stored, {})

    def test_message_on_a_closed_chat_is_409(self):
        hist = [{"role": "assistant", "content": ia._CASO_LISTO}]
        with mock.patch.object(ia, "_load_history", return_value=hist):
            with self.assertRaises(case_chat.CaseError) as ctx:
                case_chat.message(TOKEN, "hola")
        self.assertEqual(ctx.exception.status, 409)


class TestStaffReadIsLogged(unittest.TestCase):
    """«Ver conversación»: every open — and every refused attempt — writes
    one `caso` row to the clinical access log."""

    def setUp(self):
        from app.modules.turnos import booking_queue
        self.bq = booking_queue
        self.records = []
        p = mock.patch("app.core.access_log.record",
                       side_effect=lambda ident, action, dni, target_id=None, allowed=True:
                       self.records.append((action, dni, target_id, allowed)))
        self.addCleanup(p.stop)
        p.start()

    def _staff_row(self, row, err):
        return mock.patch.object(self.bq.bkr, "_staff_row_or_error", return_value=(row, err))

    def test_open_is_logged_and_returns_the_chat(self):
        data = {"resumen": {"quiere": "Control", "urgencia": "alta"},
                "mensajes": [{"role": "paciente", "texto": "Necesito un control"}]}
        with self._staff_row({"id": 9, "dni": "30111222"}, None), \
                mock.patch.object(db_chat, "case_chat_get", return_value=data):
            status, body = self.bq.staff_case_chat({"usuario": "mesa"}, 9)
        self.assertEqual(status, 200)
        self.assertEqual(body["mensajes"], data["mensajes"])
        self.assertEqual(self.records, [("caso", "30111222", 9, True)])

    def test_refused_attempt_is_logged_too(self):
        with self._staff_row(None, (403, {"error": "x"})), \
                mock.patch.object(self.bq.store, "get_booking_request", return_value={"id": 9, "dni": "30111222"}), \
                mock.patch.object(db_chat, "case_chat_get", side_effect=AssertionError("no read")):
            status, _ = self.bq.staff_case_chat({"usuario": "otro"}, 9)
        self.assertEqual(status, 403)
        self.assertEqual(self.records, [("caso", "30111222", 9, False)])

    def test_request_without_chat_is_404(self):
        with self._staff_row({"id": 9, "dni": "30111222"}, None), \
                mock.patch.object(db_chat, "case_chat_get", return_value=None):
            status, body = self.bq.staff_case_chat({"usuario": "mesa"}, 9)
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_queue_rows_carry_only_the_summary(self):
        rows = [{"id": 1}, {"id": 2}]
        with mock.patch.object(db_chat, "case_chat_summaries", return_value={2: {"quiere": "x", "urgencia": "baja"}}):
            self.bq._add_case_summaries(rows)
        self.assertEqual(rows[0], {"id": 1})
        self.assertEqual(rows[1]["caso_resumen"]["urgencia"], "baja")
        self.assertTrue(rows[1]["caso_chat"])

    def test_whole_row_reads_never_carry_the_chat(self):
        from app.core import db_wire
        out = db_wire._wire({"id": 1, "patient_note": "x", "case_chat": {"mensajes": []}}, "booking_requests")
        self.assertNotIn("case_chat", out)
        self.assertEqual(out["nota_paciente"], "x")


class TestStartWindow(unittest.TestCase):
    """Per IP only new conversations count, in their own bucket: the answers
    of one conversation are capped by code, and spending the interview's
    12/h on them left a third patient behind a carrier's shared address
    with the plain box (booking-first-paint, 2026-11-07)."""

    def setUp(self):
        from app.modules.intake import interview_patient, interview_patient_router
        from app.modules.turnos import booking_requests
        self.ip_mod, self.router = interview_patient, interview_patient_router
        _env.set_env(self, INTAKE_CASE_PER_HOUR="2", INTAKE_PER_HOUR="1")
        for bucket in (interview_patient._CASE_HITS, interview_patient._HITS):
            bucket.clear()
            self.addCleanup(bucket.clear)
        p = mock.patch.object(booking_requests, "_get_user", return_value=None)
        self.addCleanup(p.stop)
        p.start()

    def _guard(self, token):
        import asyncio
        from starlette.requests import Request
        req = Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                       "client": ("203.0.113.9", 4000)})
        return asyncio.run(self.router._case_guard(req, token))[1]

    def test_only_a_start_counts_and_never_in_the_interview_window(self):
        for _ in range(5):
            self.assertIsNone(self._guard(TOKEN), "an answer is never counted per IP")
        self.assertIsNone(self._guard(None))
        self.assertIsNone(self._guard(None))
        self.assertEqual(self._guard(None).status_code, 429)
        self.assertEqual(self.ip_mod._HITS, {}, "the interview's own window is untouched")

    def test_default_is_twenty_conversations_an_hour(self):
        _env.set_env(self, INTAKE_CASE_PER_HOUR=None)
        self.assertEqual(self.ip_mod._case_per_hour(), 20)
        _env.set_env(self, INTAKE_CASE_PER_HOUR="x")
        self.assertEqual(self.ip_mod._case_per_hour(), 20)


if __name__ == "__main__":
    unittest.main()
