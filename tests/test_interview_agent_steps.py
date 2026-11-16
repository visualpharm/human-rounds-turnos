"""test_interview_agent_steps.py — characterization of interview_agent.run_turn.

Pins what one turn does through its public entry point, with the persistence
seams (`_load_history`, `_save`, `_close`, `_log`) replaced by recorders, so the
tests need no database and can assert the ORDER of the side effects as well as
their arguments. The model is either the deterministic fake (TURNOS_FAKE_AI=1,
planted replies through the "MARKERS:" escape hatch) or `ai_client.call`
replaced by a recorder. The loop-level behaviour against a real database stays
in test_intake_agent.py.

Run:  python3 -m unittest discover -s tests -p test_interview_agent_steps.py
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
from app.core import log_pii, site_identity, theme  # noqa: E402
from app.modules.chat import chat_language  # noqa: E402
from app.modules.intake import dialogue, interview_agent as ia  # noqa: E402

EMERGENCY_CLOSE = "Andá a la guardia ahora mismo. [fin]"
CLINICAL_CLOSE = "Gracias, con esto el médico tiene lo necesario. [fin]"
OFF_TOPIC_CLOSE = ia._REDIRECT_FRASE + " Si preferís, lo dejamos acá. [fin]"


class _Anon:
    """Stands in for the pii scrubber: records what the model would receive."""

    def __init__(self):
        self.scrubbed = []

    def scrub_messages(self, messages):
        self.scrubbed.append(messages)
        return ["SCRUBBED"]

    def restore(self, texto):
        return texto.replace("<NAME>", "Juan")


class _TurnCase(unittest.TestCase):
    def setUp(self):
        _env.set_env(self, TURNOS_FAKE_AI="1", INTAKE_MAX_TURNS=None)
        self.events = []
        self.history = []
        self.logs = []
        self.interview = {"id": 7, "session_key": "interview:abc123", "entry_mode": "post_booking"}
        self.clave = "interview:abc123"
        self._patch("_load_history", side_effect=self._load)
        self._patch("_save", side_effect=lambda clave, msg: self.events.append(("save", clave, msg)))
        self._patch("_close", side_effect=lambda itv: self.events.append(("close", itv)))
        self._patch("_log", side_effect=self.logs.append)

    def _patch(self, name, **kw):
        p = mock.patch.object(ia, name, **kw)
        self.addCleanup(p.stop)
        return p.start()

    def _load(self, clave):
        self.events.append(("load", clave))
        return list(self.history)

    def kinds(self):
        return [e[0] for e in self.events]

    def saves(self):
        return [e[2] for e in self.events if e[0] == "save"]

    def closes(self):
        return [e[1] for e in self.events if e[0] == "close"]

    def turn(self, texto, **kw):
        return ia.run_turn(self.interview, texto, **kw)

    def planted(self, reply):
        return "MARKERS:" + reply


class TestSetupAndEarlyExits(_TurnCase):
    def test_key_comes_from_session_key_or_the_interview_id(self):
        self.turn("")
        self.assertEqual(self.events[0], ("load", "interview:abc123"))
        self.events.clear()
        ia.run_turn({"id": 9}, "")
        self.assertEqual(self.events[0], ("load", "interview:9"))
        self.events.clear()
        ia.run_turn(None, "")
        self.assertEqual(self.events[0], ("load", "interview:None"))

    def test_fresh_interview_without_text_gets_the_opening_and_nothing_else(self):
        with mock.patch.object(ia, "_fake_answer") as fake:
            result = self.turn("")
        fake.assert_not_called()
        opening = dialogue.copy("opening", ia._APERTURA, dialogue.state([]))
        self.assertEqual(result, {"texto": opening, "fin": False, "opciones": []})
        self.assertEqual(self.events, [
            ("load", self.clave),
            ("save", self.clave, {"role": "assistant", "content": opening,
                                  "intake_topics": ["reason"]}),
        ])

    def test_user_message_is_stripped_cut_and_saved_before_the_reply(self):
        long_text = "x" * (ia.MAX_INPUT_TEXT + 50)
        self.turn("   " + long_text + "   ")
        self.assertEqual(self.kinds(), ["load", "save", "save"])
        self.assertEqual(self.events[1][2], {"role": "user", "content": "x" * ia.MAX_INPUT_TEXT})
        self.assertEqual(self.events[2][2]["role"], "assistant")

    def test_whitespace_only_text_is_no_message_and_with_an_image_it_is_multipart(self):
        self.turn("   ")
        self.assertEqual(self.saves()[0]["role"], "assistant")  # opening, no user row
        self.events.clear()
        image = {"mime": "image/png", "data_uri": "data:image/png;base64,QUFB"}
        self.turn("", imagen=image)
        self.assertEqual(self.saves()[0], {"role": "user", "content": [
            {"type": "text", "text": "(foto adjunta)"},
            {"type": "image_url", "image_url": {"url": image["data_uri"]}}]})
        self.assertEqual(self.saves()[1]["role"], "assistant")

    def test_reopened_seed_call_skips_the_opening_and_uses_the_fixed_question(self):
        result = self.turn("", reabierta=True)
        self.assertEqual(result["texto"], ia._FAKE_REABIERTA)
        self.assertEqual(self.kinds(), ["load", "save"])
        self.assertEqual(self.saves()[0]["intake_outcome"], "active")

    def test_history_with_no_new_text_does_not_reopen_the_opening(self):
        self.history = [{"role": "assistant", "content": "¿Qué te trae?"}]
        result = self.turn("")
        self.assertEqual(result["texto"], ia._FAKE_PREGUNTAS[1])

    def test_urgent_home_routes_to_the_emergency_number_and_closes_red(self):
        with mock.patch.object(site_identity, "get", return_value="107") as get, \
                mock.patch.object(ia, "_fake_answer") as fake:
            result = self.turn("Después del golpe tengo los dedos azules y no siento la mano.")
        fake.assert_not_called()
        get.assert_called_once_with("emergency_number")
        ledger = dialogue.state([{"role": "user", "content":
                                  "Después del golpe tengo los dedos azules y no siento la mano."}])
        content = dialogue.copy(
            "urgent_home", "Buscá atención presencial urgente en una guardia ahora "
            "o llamá al {number}. No esperes al turno.", ledger, number="107") + " [fin]"
        self.assertEqual(result, {"texto": content[:-len(" [fin]")], "fin": True, "opciones": []})
        self.assertEqual(self.kinds(), ["load", "save", "save", "close"])
        self.assertEqual(self.saves()[1], {"role": "assistant", "content": content,
                                           "intake_outcome": "urgent"})
        self.assertEqual(self.closes(), [{**self.interview, "triage": "ROJO"}])
        self.assertNotIn("triage", self.interview)

    def test_urgent_home_without_a_configured_number_uses_the_generic_phrase(self):
        with mock.patch.object(site_identity, "get", return_value=""):
            result = self.turn("Tengo los dedos azules y no siento la mano.")
        self.assertIn("el número local de emergencias", result["texto"])

    def test_urgent_qr_tells_the_patient_to_alert_staff_not_to_call(self):
        self.interview["entry_mode"] = "qr"
        with mock.patch.object(site_identity, "get") as get:
            result = self.turn("Tengo los dedos azules y no siento la mano.")
        get.assert_not_called()
        self.assertTrue(result["fin"])
        self.assertIn("Avisá al personal", result["texto"])
        self.assertEqual(self.saves()[1]["intake_outcome"], "urgent")
        self.assertEqual(self.closes()[0]["triage"], "ROJO")

    def test_urgent_wins_over_a_stop_request(self):
        result = self.turn("Tengo los dedos azules y no siento la mano. Prefiero terminar.")
        self.assertEqual(self.saves()[1]["intake_outcome"], "urgent")
        self.assertEqual(self.closes()[0]["triage"], "ROJO")
        self.assertTrue(result["fin"])

    def test_stop_request_closes_as_incomplete_without_calling_the_model(self):
        with mock.patch.object(ia, "_fake_answer") as fake:
            result = self.turn("Prefiero terminar.")
        fake.assert_not_called()
        ledger = dialogue.state([{"role": "user", "content": "Prefiero terminar."}])
        incomplete = dialogue.copy("incomplete", ia._strip_fin(ia._CIERRE_LIMITE), ledger) + " [fin]"
        self.assertEqual(result, {"texto": ia._strip_fin(incomplete), "fin": True, "opciones": []})
        self.assertEqual(self.kinds(), ["load", "save", "save", "close"])
        self.assertEqual(self.saves()[1], {"role": "assistant", "content": incomplete,
                                           "intake_outcome": "incomplete"})
        self.assertIs(self.closes()[0], self.interview)

    def test_turn_cap_closes_only_past_the_limit(self):
        _env.set_env(self, INTAKE_MAX_TURNS="2")
        self.history = [{"role": "user", "content": "uno"}, {"role": "assistant", "content": "a"}]
        within = self.turn("dos")
        self.assertFalse(within["fin"])
        self.assertEqual(self.closes(), [])
        self.history += [{"role": "user", "content": "dos"}, {"role": "assistant", "content": "b"}]
        self.events.clear()
        with mock.patch.object(ia, "_fake_answer") as fake:
            over = self.turn("tres")
        fake.assert_not_called()
        self.assertTrue(over["fin"])
        self.assertNotIn("[fin]", over["texto"])
        self.assertEqual(self.kinds(), ["load", "save", "save", "close"])
        self.assertEqual(self.saves()[1]["intake_outcome"], "incomplete")
        self.assertIs(self.closes()[0], self.interview)

    def test_ai_unavailable_gives_the_apology_without_closing(self):
        _env.set_env(self, TURNOS_FAKE_AI=None)
        with mock.patch.object(ai_client, "available", return_value=False), \
                mock.patch.object(ai_client, "call") as call:
            result = self.turn("me duele la cabeza")
        call.assert_not_called()
        apology = dialogue.copy("unavailable", ia._DISCULPA, dialogue.state(
            [{"role": "user", "content": "me duele la cabeza"}]))
        self.assertEqual(result, {"texto": apology, "fin": False, "opciones": []})
        self.assertEqual(self.kinds(), ["load", "save", "save"])
        self.assertEqual(self.saves()[1], {"role": "assistant", "content": apology,
                                           "intake_outcome": "incomplete"})

    def test_open_breaker_gives_the_apology_too(self):
        _env.set_env(self, TURNOS_FAKE_AI=None)
        with mock.patch.object(ai_client, "available", return_value=True), \
                mock.patch.object(ai_health, "available", return_value=False), \
                mock.patch.object(ai_client, "call") as call:
            result = self.turn("me duele la cabeza")
        call.assert_not_called()
        self.assertFalse(result["fin"])
        self.assertEqual(self.saves()[1]["intake_outcome"], "incomplete")
        self.assertEqual(self.closes(), [])

    def test_fake_mode_never_asks_whether_the_ai_is_available(self):
        with mock.patch.object(ai_client, "available", side_effect=AssertionError("asked")), \
                mock.patch.object(ai_health, "available", side_effect=AssertionError("asked")):
            result = self.turn("me duele la cabeza")
        self.assertEqual(result["texto"], ia._FAKE_PREGUNTAS[0])


class TestRealModelCall(_TurnCase):
    def setUp(self):
        super().setUp()
        _env.set_env(self, TURNOS_FAKE_AI=None, INTAKE_INTERVIEW_MAX_TOKENS=None,
                     INTAKE_INTERVIEW_REASONING_EFFORT=None)
        self.anon = _Anon()
        self.calls = []
        self.reports = []
        self.response = {"choices": [{"message": {"content": "¿Desde cuándo te duele?"}}]}
        self.prompt_args = []
        self._patch("_model", return_value="model-x")

        def system_prompt(interview, ledger=None, scrubber=None):
            self.prompt_args.append((interview, ledger, scrubber))
            return "SYS"

        self._patch("_system_prompt", side_effect=system_prompt)
        for target, kw in (
                (ai_client, {"available": {"return_value": True}}),
                (ai_health, {"available": {"return_value": True},
                             "report": {"side_effect": self.reports.append}}),
                (ai_client, {"call": {"side_effect": self._call}})):
            for name, opts in kw.items():
                p = mock.patch.object(target, name, **opts)
                self.addCleanup(p.stop)
                p.start()
        p = mock.patch.object(ia.pii, "for_dni", return_value=self.anon)
        self.addCleanup(p.stop)
        self.for_dni = p.start()

    def _call(self, body, **kw):
        self.calls.append((body, kw))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def test_request_shape_and_reply_flow(self):
        self.history = [
            {"role": "assistant", "content": "¿Qué te trae?", "intake_topics": ["reason"]},
            {"role": "user", "content": "Me duele la cabeza"},
            {"role": "system", "content": "ignored"},
            {"role": "assistant"},
        ]
        self.interview["dni"] = "30111222"
        result = self.turn("Desde ayer")
        self.for_dni.assert_called_once_with("30111222")
        interview, ledger, scrubber = self.prompt_args[0]
        self.assertIs(interview, self.interview)
        self.assertIs(scrubber, self.anon)
        self.assertEqual(ledger, dialogue.state(self.history + [{"role": "user", "content": "Desde ayer"}]))
        sent = self.anon.scrubbed[0]
        self.assertEqual(sent, [
            {"role": "system", "content": "SYS" + chat_language.rule("Me duele la cabeza")},
            {"role": "assistant", "content": "¿Qué te trae?"},
            {"role": "user", "content": "Me duele la cabeza"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "Desde ayer"},
            {"role": "system", "content": ia._RECORDATORIO_GUARDRAILS},
        ])
        body, kw = self.calls[0]
        self.assertEqual(body, {"model": "model-x", "messages": ["SCRUBBED"],
                                "temperature": 0.2, "max_tokens": 500})
        self.assertEqual(kw, {"timeout": 45, "headers_extra": {
            "X-Title": theme.brand_name() + " - entrevista previa"}})
        self.assertEqual(self.reports, [None])
        self.assertEqual(result, {"texto": "¿Desde cuándo te duele?", "fin": False, "opciones": []})
        self.assertEqual(self.saves()[1]["intake_outcome"], "active")

    def test_language_rule_follows_the_first_patient_text_or_the_current_one(self):
        self.turn("my head hurts")
        self.assertEqual(self.anon.scrubbed[0][0]["content"], "SYS" + chat_language.rule("my head hurts"))
        self.history = [{"role": "assistant", "content": "¿Qué te trae?"}]
        self.anon.scrubbed.clear()
        ia.run_turn(self.interview, "", reabierta=True)
        self.assertEqual(self.anon.scrubbed[0][0]["content"], "SYS\n\n" + ia._REABIERTA_INSTRUCCION)

    def test_reopened_instruction_comes_after_the_language_rule(self):
        self.history = [{"role": "user", "content": "me duele"}, {"role": "assistant", "content": "ok"}]
        ia.run_turn(self.interview, "", reabierta=True)
        self.assertEqual(self.anon.scrubbed[0][0]["content"],
                         "SYS" + chat_language.rule("me duele") + "\n\n" + ia._REABIERTA_INSTRUCCION)
        self.assertEqual(self.anon.scrubbed[0][-2]["role"], "assistant")  # no user row appended

    def test_image_turn_sends_the_multipart_content(self):
        image = {"mime": "image/png", "data_uri": "data:image/png;base64,QUFB"}
        self.turn("una mancha", imagen=image)
        user = self.anon.scrubbed[0][-2]
        self.assertEqual(user["role"], "user")
        self.assertEqual(user["content"][1]["image_url"]["url"], image["data_uri"])

    def test_inference_options_and_dni_default(self):
        _env.set_env(self, INTAKE_INTERVIEW_MAX_TOKENS="9000", INTAKE_INTERVIEW_REASONING_EFFORT="low")
        self.turn("hola")
        self.for_dni.assert_called_once_with(None)
        self.assertEqual(self.calls[0][0]["max_tokens"], 4096)
        self.assertEqual(self.calls[0][0]["reasoning"], {"effort": "low", "exclude": True})

    def test_restored_reply_is_filtered_saved_and_returned(self):
        self.response = {"choices": [{"message": {"content": "  ¿Cómo sigue <NAME>? [opciones: Bien | Mal]  "}}]}
        result = self.turn("hola")
        self.assertEqual(result["texto"], "¿Cómo sigue Juan?")
        self.assertEqual(result["opciones"], ["Bien", "Mal"])
        self.assertEqual(self.saves()[1]["content"], "¿Cómo sigue Juan? [opciones: Bien | Mal]")

    def test_slot_is_released_after_success_and_after_a_failing_call(self):
        slots = mock.Mock()
        slots.acquire.return_value = True
        with mock.patch.object(ia, "_MODEL_SLOTS", slots):
            self.turn("hola")
            slots.acquire.assert_called_once_with(blocking=False)
            slots.release.assert_called_once_with()
            slots.reset_mock()
            self.response = RuntimeError("boom")
            result = self.turn("hola otra vez")
        slots.release.assert_called_once_with()
        self.assertEqual(result["fin"], False)

    def test_no_free_slot_degrades_to_the_apology_without_calling_or_releasing(self):
        slots = mock.Mock()
        slots.acquire.return_value = False
        with mock.patch.object(ia, "_MODEL_SLOTS", slots):
            result = self.turn("hola")
        self.assertEqual(self.calls, [])
        slots.release.assert_not_called()
        self.assertEqual(len(self.reports), 1)
        self.assertIsInstance(self.reports[0], RuntimeError)
        self.assertEqual(str(self.reports[0]), "demasiadas entrevistas en curso")
        self.assertEqual(result["texto"], dialogue.copy("unavailable", ia._DISCULPA, dialogue.state(
            [{"role": "user", "content": "hola"}])))

    def test_failures_report_to_health_log_and_degrade_to_the_apology(self):
        cases = {
            "call raises": RuntimeError("down"),
            "truncated": {"choices": [{"finish_reason": "length", "message": {"content": "algo"}}]},
            "empty": {"choices": [{"message": {"content": "   "}}]},
            "missing content": {"choices": [{"message": {}}]},
            "malformed": {},
        }
        for name, response in cases.items():
            with self.subTest(name):
                self.response = response
                self.reports.clear()
                self.logs.clear()
                self.events.clear()
                result = self.turn("hola")
                apology = dialogue.copy("unavailable", ia._DISCULPA, dialogue.state(
                    [{"role": "user", "content": "hola"}]))
                self.assertEqual(result, {"texto": apology, "fin": False, "opciones": []})
                self.assertEqual(self.kinds(), ["load", "save", "save"])
                self.assertEqual(self.saves()[1], {"role": "assistant", "content": apology,
                                                   "intake_outcome": "incomplete"})
                error = self.reports[-1]
                self.assertIsInstance(error, Exception)
                self.assertEqual(self.logs, [
                    f"llamada al modelo falló ({log_pii.key(self.clave)}): {log_pii.exc(error)}"])
        # A response that reached the parser reports success first, then the failure.
        self.response = cases["truncated"]
        self.reports.clear()
        self.turn("hola")
        self.assertIsNone(self.reports[0])
        self.assertIsInstance(self.reports[1], ValueError)
        self.assertEqual(str(self.reports[1]), "truncated interview response")
        self.response = cases["empty"]
        self.reports.clear()
        self.turn("hola")
        self.assertEqual(str(self.reports[1]), "empty interview response")


class TestReplyReview(_TurnCase):
    """Output filter, repeat guard, empty-interview backstop and persistence,
    driven by planted replies (MARKERS:) in fake mode."""

    def test_code_shaped_reply_is_swapped_before_persisting(self):
        result = self.turn(self.planted("Dale: ```rm -rf /```"))
        self.assertEqual(result["texto"], ia._REDIRECT_ESTANDAR)
        self.assertEqual(self.saves()[1], {"role": "assistant", "content": ia._REDIRECT_ESTANDAR,
                                           "intake_topics": [], "intake_outcome": "active"})

    def test_plain_reply_is_saved_with_topics_and_options_split_for_the_caller(self):
        result = self.turn(self.planted("¿Desde cuándo? [pregunta: onset] [opciones: Hoy | Ayer]"))
        self.assertEqual(result, {"texto": "¿Desde cuándo?", "fin": False, "opciones": ["Hoy", "Ayer"]})
        self.assertEqual(self.saves()[1], {
            "role": "assistant", "content": "¿Desde cuándo?  [opciones: Hoy | Ayer]",
            "intake_topics": ["onset"], "intake_outcome": "active"})
        self.assertEqual(self.closes(), [])

    def test_reply_with_two_questions_is_replaced_and_stays_open(self):
        result = self.turn(self.planted("¿Desde cuándo? ¿Y dónde?"))
        again = dialogue.copy("additional", "¿Hay algo más que quieras agregar o corregir para la consulta?",
                              dialogue.state([{"role": "user", "content": self.planted("¿Desde cuándo? ¿Y dónde?")}]))
        self.assertEqual(result["texto"], again)
        saved = self.saves()[1]
        self.assertEqual(saved["intake_topics"], ["additional"])
        self.assertEqual(saved["intake_outcome"], "active")

    def test_guarded_closing_is_stored_as_incomplete_and_closes_the_interview(self):
        guarded = ("Dejamos la entrevista acá. [fin]", [], True)
        with mock.patch.object(dialogue, "guard_reply", return_value=guarded) as guard:
            result = self.turn(self.planted("algo"))
        guard.assert_called_once()
        self.assertEqual(guard.call_args[0][0], "algo")
        self.assertEqual(guard.call_args[0][1], dialogue.state(
            [{"role": "user", "content": self.planted("algo")}]))
        self.assertTrue(result["fin"])
        self.assertEqual(self.saves()[1]["intake_outcome"], "incomplete")
        self.assertIs(self.closes()[0], self.interview)

    def test_clinical_closing_is_complete_and_closes(self):
        self.history = [{"role": "assistant", "content": "¿Qué te trae?"},
                        {"role": "user", "content": "Me duele la rodilla"},
                        {"role": "assistant", "content": "¿Desde cuándo?"},
                        {"role": "user", "content": "Desde ayer"},
                        {"role": "assistant", "content": "¿Algo más?"}]
        result = self.turn(self.planted(CLINICAL_CLOSE))
        self.assertEqual(result, {"texto": "Gracias, con esto el médico tiene lo necesario.",
                                  "fin": True, "opciones": []})
        self.assertEqual(self.saves()[1], {"role": "assistant", "content": CLINICAL_CLOSE,
                                           "intake_topics": [], "intake_outcome": "complete"})
        self.assertEqual(self.kinds(), ["load", "save", "save", "close"])
        self.assertIs(self.closes()[0], self.interview)

    def test_emergency_closing_skips_the_repeat_guard_and_closes_red(self):
        reply = "Andá a la guardia ahora mismo. [pregunta: onset] [fin]"
        with mock.patch.object(dialogue, "guard_reply") as guard:
            result = self.turn(self.planted(reply))
        guard.assert_not_called()
        self.assertEqual(result, {"texto": "Andá a la guardia ahora mismo.", "fin": True, "opciones": []})
        self.assertEqual(self.saves()[1], {
            "role": "assistant", "content": "Andá a la guardia ahora mismo.  [fin]",
            "intake_topics": [], "intake_outcome": "urgent"})
        self.assertEqual(self.closes(), [{**self.interview, "triage": "ROJO"}])

    def test_emergency_closing_is_never_overridden_by_the_empty_interview_backstop(self):
        # A redirect that also routes to the emergency room: with no clinical
        # content yet, only the emergency exemption keeps it from being swapped.
        reply = ia._REDIRECT_FRASE + " Andá a la guardia ahora mismo. [fin]"
        result = self.turn(self.planted(reply))
        self.assertTrue(result["fin"])
        self.assertEqual(result["texto"], reply[:-len(" [fin]")])
        self.assertEqual(self.saves()[1]["intake_outcome"], "urgent")
        self.assertEqual(self.closes()[0]["triage"], "ROJO")

    def test_early_off_topic_closing_is_turned_into_the_opening_question(self):
        self.history = [{"role": "assistant", "content": "Soy el asistente. ¿Qué te trae a esta consulta?"}]
        result = self.turn(self.planted(OFF_TOPIC_CLOSE))
        expected = ia._REDIRECT_FRASE + " ¿Qué te trae a esta consulta?"
        self.assertEqual(result, {"texto": expected, "fin": False, "opciones": []})
        self.assertEqual(self.saves()[1]["content"], expected)
        self.assertEqual(self.saves()[1]["intake_outcome"], "active")
        self.assertEqual(self.closes(), [])

    def test_backstop_falls_back_to_the_stock_opening_without_one_in_history(self):
        result = self.turn(self.planted(OFF_TOPIC_CLOSE))
        self.assertEqual(result["texto"], ia._REDIRECT_FRASE + " " + ia._APERTURA_FALLBACK)
        self.assertFalse(result["fin"])

    def test_off_topic_closing_is_allowed_from_the_third_patient_turn(self):
        redirect = {"role": "assistant", "content": ia._REDIRECT_ESTANDAR}
        self.history = [{"role": "user", "content": "uno"}, redirect,
                        {"role": "user", "content": "dos"}, redirect]
        result = self.turn(self.planted(OFF_TOPIC_CLOSE))
        self.assertTrue(result["fin"])
        self.assertEqual(self.saves()[1]["intake_outcome"], "complete")
        self.assertIs(self.closes()[0], self.interview)

    def test_off_topic_closing_is_still_blocked_on_the_second_turn(self):
        redirect = {"role": "assistant", "content": ia._REDIRECT_ESTANDAR}
        self.history = [{"role": "user", "content": "uno"}, redirect]
        result = self.turn(self.planted(OFF_TOPIC_CLOSE))
        self.assertFalse(result["fin"])
        self.assertEqual(self.closes(), [])

    def test_a_model_failure_in_fake_mode_still_degrades_and_reports(self):
        reports = []
        with mock.patch.object(ia, "_fake_answer", side_effect=ValueError("bad")), \
                mock.patch.object(ai_health, "report", side_effect=reports.append):
            result = self.turn("hola")
        self.assertFalse(result["fin"])
        self.assertEqual(len(reports), 1)
        self.assertIsInstance(reports[0], ValueError)
        self.assertEqual(self.saves()[1]["intake_outcome"], "incomplete")
        self.assertEqual(len(self.logs), 1)

    def test_fake_success_does_not_report_to_the_breaker(self):
        with mock.patch.object(ai_health, "report") as report:
            self.turn("hola")
        report.assert_not_called()


if __name__ == "__main__":
    unittest.main()
