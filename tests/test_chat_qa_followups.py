"""test_chat_qa_followups.py — QA 2026-10-25 P01, P04, P12.

codex-pinamar-chat-qa-20261025 (source e8b70d7):

- P01: «¿Cómo puedo pedir un turno por este chat? Sólo pasos, no reserves.»
  got three steps plus a wall of slot links with no specialty chosen —
  `_requires_slot_grounding` read it as a slot request and forced a lookup.
  An explicit «just the steps» forces nothing and gets no slot cards.
- P04: «Ahora en español, sólo el primer paso» after an English first
  message: the model switched, the application's own link label stayed
  «Services and appointments». An explicit request now moves both.
- P12: «vas a poder gestionar todo desde Mis turnos» — after logging in a
  person manages their own appointments and those of the people in their
  care, never «everything».

No Postgres needed: history and persistence are an in-memory list.
"""

import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402
_env.process_defaults(TURNOS_SKIP_HSI="1")

from app.modules.chat import chat_agent  # noqa: E402
from app.modules.chat import chat_language  # noqa: E402
from app.modules.chat import chat_tools  # noqa: E402

P01 = "¿Cómo puedo pedir un turno por este chat? Sólo pasos, no reserves."
P04_FIRST = "Please explain the appointment steps in exactly three short bullets. Do not book anything."
P04_SWITCH = "Ahora en español, sólo el primer paso, en una frase corta."


def _turn(texto, respuesta, historia=(), full=True):
    env = {"TURNOS_FAKE_CHAT_AI": "0", "TURNOS_FAKE_AI": "0"}
    with patch.object(chat_agent, "_call_chat",
                      return_value={"choices": [{"message": {"content": respuesta}}]}), \
         patch.object(chat_agent, "_save"), \
         patch.object(chat_agent, "_load_history", return_value=[dict(m) for m in historia]), \
         patch.object(chat_agent, "_first_user_message",
                      return_value=next((m["content"] for m in historia
                                         if m.get("role") == "user"), "")), \
         patch.object(chat_agent, "_turns_used", return_value=1), \
         patch.object(chat_agent, "_memory_block", return_value=""), \
         patch.object(chat_agent, "_env", side_effect=lambda k, d="": env.get(k, d)), \
         patch("app.ai.ai_client.available", return_value=True), \
         patch("app.ai.ai_client.primary_key", return_value="test-key"), \
         patch("app.ai.ai_health.available", return_value=True), \
         patch("app.ai.ai_health.report"):
        if full:
            return chat_agent.chat_full(None, texto, "test:qa", "web")
        return chat_agent._run_turn(None, texto, "test:qa", "web")


class TestStepsOnlyP01(unittest.TestCase):
    def test_steps_only_forces_no_lookup(self):
        self.assertFalse(chat_agent._requires_slot_grounding(P01))
        for texto in ("Just the steps, please: how do I book an appointment?",
                      "Só os passos para marcar uma consulta, por favor."):
            self.assertFalse(chat_agent._requires_slot_grounding(texto), texto)
        # A real request still grounds on real slots.
        self.assertTrue(chat_agent._requires_slot_grounding("Quiero sacar un turno con pediatría."))

    def test_steps_only_reply_carries_no_slot_cards(self):
        fila = {"servicio_id": "medicina-general-pinamar", "nombre": "Medicina general",
                "centro": "CAPS Pinamar", "dia": "2026-11-02", "hora": "08:20",
                "etiqueta": "Lunes 2 de noviembre, 08:20",
                "href": "/service/medicina-general-pinamar/book?slot=2026-11-02T08:20",
                "servicio_href": "/service/medicina-general-pinamar"}
        turno = {"texto": "1. Elegí el servicio.\n2. Elegí el horario.\n3. Confirmá.",
                 "tools": [], "idioma": "es"}
        enlaces = [{"label": "Medicina general", "href": fila["href"]},
                   {"label": "Servicios y turnos", "href": "/"}]
        for texto, con_turnos in ((P01, False), ("Quiero sacar un turno con medicina general.", True)):
            with self.subTest(texto=texto), \
                 patch.object(chat_agent, "_run_turn", return_value=dict(turno)), \
                 patch.object(chat_agent, "_slots_de_las_tools", return_value=([fila], "/more")), \
                 patch.object(chat_tools, "enlaces_para", return_value=list(enlaces)):
                out = chat_agent.chat_full(None, texto, "test:qa", "web")
            self.assertEqual(bool(out.get("turnos")), con_turnos)
            hrefs = [e["href"] for e in out["enlaces"]]
            self.assertEqual(any("slot=" in h for h in hrefs), con_turnos)
            self.assertIn("/", hrefs)


class TestExplicitLanguageSwitchP04(unittest.TestCase):
    def test_requests_are_recognised(self):
        casos = {
            P04_SWITCH: "es",
            "Ahora respondeme en español": "es",
            "¿Podés responderme en inglés?": "en",
            "Please reply in English.": "en",
            "In English please": "en",
            "Can you speak Spanish?": "es",
            "Responda em português, por favor": "pt",
            "Rispondi in italiano": "it",
            "Répondez en français s'il vous plaît": "fr",
            "Antworte bitte auf Deutsch": "de",
            "\u041e\u0442\u0432\u0435\u0447\u0430\u0439 \u043f\u043e-\u0440\u0443\u0441\u0441\u043a\u0438": "ru",
            "\u0412\u0456\u0434\u043f\u043e\u0432\u0456\u0434\u0430\u0439 \u0443\u043a\u0440\u0430\u0457\u043d\u0441\u044c\u043a\u043e\u044e, \u0431\u0443\u0434\u044c \u043b\u0430\u0441\u043a\u0430": "uk",
            "英語でお願いします": "en",
            "请用英语回答": "en",
        }
        for texto, code in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(chat_language.requested(texto), code)

    def test_a_mentioned_language_is_not_a_request(self):
        for texto in ("¿El médico habla inglés?", "Does the doctor speak English?",
                      "I want to continue with the French doctor.",
                      "Necesito un turno, ¿dónde queda el CAPS?", P04_FIRST, P01):
            with self.subTest(texto=texto):
                self.assertEqual(chat_language.requested(texto), "")

    def test_the_switch_moves_the_rule(self):
        regla = chat_agent._language_rule(
            [{"role": "user", "content": P04_FIRST}], P04_SWITCH)
        self.assertIn("explicitly asked to continue in Español [es]", regla)
        self.assertNotIn(P04_FIRST, regla)

    def test_writing_in_another_language_is_not_a_switch(self):
        regla = chat_agent._language_rule(
            [{"role": "user", "content": "I need an ophthalmologist."}],
            "¿Y un turno para el lunes?")
        self.assertIn("required response language is English", regla)

    def test_the_switch_persists_and_moves_the_labels(self):
        historia = [{"role": "user", "content": P04_FIRST},
                    {"role": "assistant", "content": "1. Pick a service."}]
        out = _turn(P04_SWITCH, "Primero elegí la especialidad.", historia)
        labels = [e["label"] for e in out["enlaces"]]
        self.assertIn("Servicios y turnos", labels)
        self.assertNotIn("Services and appointments", labels)
        # A later message in Spanish without a new request stays Spanish.
        historia += [{"role": "user", "content": P04_SWITCH},
                     {"role": "assistant", "content": "Primero elegí la especialidad."}]
        later = _turn("¿Y después?", "Después elegí el horario.", historia, full=False)
        self.assertEqual(later["idioma"], "es")


class TestManagingAfterLoginP12(unittest.TestCase):
    def test_the_prompt_scopes_it_to_own_and_dependents(self):
        reglas, _ = chat_agent._prompt_with_knowledge(None, "¿Puedo cancelar el turno de otra persona?", "es")
        self.assertIn("sus propios turnos y los de las personas a su cargo", reglas)
        self.assertIn("nunca digas «todo»", reglas)


if __name__ == "__main__":
    unittest.main()
