"""test_chat_coverage.py — an unknown coverage stays unknown.

QA 2026-10-25 P05 (codex-pinamar-chat-qa-20261025, ui/P05/receipt.json, live
on pinamar.humanrounds.org): «¿La cobertura sintética A cubre dermatología?»
got «la Cobertura Universal de Salud (CUS), antes llamada Cobertura Sintética,
garantiza el acceso… incluyendo Dermatología». No reference said any of it.
When the patient asks about a coverage, a sentence claiming what it covers,
guarantees or used to be called survives only if it names one of the turn's
RAG references; the reply then leads with «no puedo confirmarlo».

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

from app.core import i18n  # noqa: E402
from app.modules.chat import chat_agent  # noqa: E402

P05_QUESTION = "¿La cobertura sintética A cubre dermatología?"
# The live answer, verbatim (ui/P05/receipt.json).
P05_ANSWER = (
    "¡Hola! Te cuento que la Cobertura Universal de Salud (CUS), antes llamada "
    "Cobertura Sintética, garantiza el acceso a las especialidades en el sistema "
    "público de Pinamar, incluyendo **Dermatología**.\n\n"
    "Para atenderte en **Dermatología** en el **Hospital de Pinamar**, necesitás "
    "una orden médica. Si todavía no la tenés, el primer paso es pedir un turno "
    "con **Medicina general** para que te hagan la derivación.")


def _turn(texto, respuesta, fuentes=()):
    def _prompt(*_a, **_k):
        return "sistema", list(fuentes)

    env = {"TURNOS_FAKE_CHAT_AI": "0", "TURNOS_FAKE_AI": "0"}
    with patch.object(chat_agent, "_call_chat",
                      return_value={"choices": [{"message": {"content": respuesta}}]}), \
         patch.object(chat_agent, "_prompt_with_knowledge", side_effect=_prompt), \
         patch.object(chat_agent, "_save"), \
         patch.object(chat_agent, "_load_history", return_value=[]), \
         patch.object(chat_agent, "_first_user_message", return_value=""), \
         patch.object(chat_agent, "_turns_used", return_value=1), \
         patch.object(chat_agent, "_memory_block", return_value=""), \
         patch.object(chat_agent, "_requires_slot_grounding", return_value=False), \
         patch.object(chat_agent, "_requires_catalog_routing", return_value=False), \
         patch.object(chat_agent, "_env", side_effect=lambda k, d="": env.get(k, d)), \
         patch("app.ai.ai_client.available", return_value=True), \
         patch("app.ai.ai_client.primary_key", return_value="test-key"), \
         patch("app.ai.ai_health.available", return_value=True), \
         patch("app.ai.ai_health.report"):
        return chat_agent._run_turn(None, texto, "test:coverage", "web")


class TestUnknownCoverageStaysUnknown(unittest.TestCase):
    def test_p05_invented_equivalence_and_guarantee_are_dropped(self):
        out = _turn(P05_QUESTION, P05_ANSWER)
        texto = out["texto"]
        self.assertTrue(texto.startswith(i18n.t_or("assistant.coverage_unknown", "\0", "es")), texto)
        for invented in ("CUS", "antes llamada", "garantiza"):
            self.assertNotIn(invented, texto)
        # The navigation the patient can act on stays.
        self.assertIn("orden médica", texto)
        self.assertIn("Medicina general", texto)

    def test_a_claim_backed_by_a_reference_stays(self):
        respuesta = ("El Certificado Único de Discapacidad cubre el 100% de las "
                     "prestaciones de rehabilitación relacionadas con lo certificado.")
        out = _turn("¿Qué cobertura me da el certificado de discapacidad?", respuesta,
                    fuentes=[{"titulo": "Certificado Único de Discapacidad (CUD)",
                              "jurisdiccion": "nacion", "url": None, "organismo": None}])
        self.assertEqual(out["texto"], respuesta)

    def test_honest_answer_is_untouched(self):
        respuesta = ("No puedo confirmar qué incluye esa cobertura. Podés sacar el "
                     "turno de Medicina general en el CAPS.")
        out = _turn(P05_QUESTION, respuesta)
        self.assertEqual(out["texto"], respuesta)

    def test_not_a_coverage_question(self):
        respuesta = "Dermatología atiende los martes; la consulta cubre la evaluación inicial."
        out = _turn("¿Qué días atiende dermatología?", respuesta)
        self.assertEqual(out["texto"], respuesta)

    def test_other_languages(self):
        out = _turn("Does my insurance plan A cover dermatology?",
                    "Plan A covers dermatology in full. You can book with general medicine first.")
        self.assertNotIn("covers dermatology", out["texto"])
        self.assertIn("general medicine", out["texto"])

    def test_the_prompt_says_so(self):
        reglas, _ = chat_agent._prompt_with_knowledge(None, P05_QUESTION, "es")
        self.assertIn("Nunca identifiques un nombre de cobertura", reglas)
        self.assertIn("No pidas fotos", reglas)


if __name__ == "__main__":
    unittest.main()
