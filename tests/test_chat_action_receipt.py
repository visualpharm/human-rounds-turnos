"""test_chat_action_receipt.py — a booking the engine refused is never told as done.

QA 2026-10-25 P09 (codex-pinamar-chat-qa-20261025, offline-adversarial-
boundaries.py): an anonymous visitor's request_appointment was denied by the
engine, and the model's next line «Listo, tu turno quedó confirmado.» still
reached the patient. A completed booking or cancellation comes only from the
domain's own receipt; after a refused attempt a success claim is replaced by
what actually happened.

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
from app.modules.chat import chat_tools  # noqa: E402

USER = {"dni": "30999888", "nombre": "Delia", "apellido": "Suárez"}
CLAIM = "Listo, tu turno quedó confirmado."
BOOK = {"tool_calls": [{"id": "book-1", "function": {
    "name": "request_appointment",
    "arguments": '{"service_id": "dermatologia", "date": "2026-10-26", "time": "09:00"}'}}]}
LIST = {"tool_calls": [{"id": "list-1", "function": {"name": "my_requests", "arguments": "{}"}}]}


def _turn(usuario, script, texto="Confirmo.", tools=None, full=False):
    """One real turn: the model says what `script` lists, in order."""
    respuestas = [{"choices": [{"message": m}]} for m in script]
    llamadas = []

    def _model(*_a, **_k):
        return respuestas.pop(0)

    def _tool(nombre, resultado):
        def impl(*_a, **_k):
            llamadas.append(nombre)
            return resultado
        return impl

    fns = dict(chat_tools.TOOL_FNS)
    for nombre, resultado in (tools or {}).items():
        fns[nombre] = _tool(nombre, resultado)
    historia = []
    env = {"TURNOS_FAKE_CHAT_AI": "0", "TURNOS_FAKE_AI": "0"}
    with patch.object(chat_agent, "_call_chat", side_effect=_model), \
         patch.object(chat_agent, "_save", side_effect=lambda c, k, m: historia.append(dict(m))), \
         patch.object(chat_agent, "_load_history", return_value=[]), \
         patch.object(chat_agent, "_first_user_message", return_value=""), \
         patch.object(chat_agent, "_turns_used", return_value=1), \
         patch.object(chat_agent, "_memory_block", return_value=""), \
         patch.object(chat_agent, "_env", side_effect=lambda k, d="": env.get(k, d)), \
         patch.object(chat_tools, "TOOL_FNS", fns), \
         patch.object(chat_tools, "confirm_booking_reply", return_value=None), \
         patch("app.ai.ai_client.available", return_value=True), \
         patch("app.ai.ai_client.primary_key", return_value="test-key"), \
         patch("app.ai.ai_health.available", return_value=True), \
         patch("app.ai.ai_health.report"):
        if full:
            out = chat_agent.chat_full(usuario, texto, "test:receipt", "web")
        else:
            out = chat_agent._run_turn(usuario, texto, "test:receipt", "web")
    return out, llamadas, historia


class TestRefusedActionIsNeverToldAsDone(unittest.TestCase):
    def test_anonymous_denied_booking_p09(self):
        out, llamadas, historia = _turn(None, [BOOK, {"content": CLAIM}], full=True)
        self.assertEqual(llamadas, [], "the anonymous booking must not run")
        self.assertNotIn("confirmado", out["texto"])
        self.assertEqual(out["texto"], i18n.t_or("assistant.action_needs_login", "\0", "es"))
        self.assertTrue(out["login"])
        # What the patient read is what the history keeps.
        self.assertNotIn(CLAIM, [m.get("content") for m in historia])

    def test_logged_in_refused_booking(self):
        out, llamadas, _ = _turn(USER, [BOOK, {"content": CLAIM}],
                                 tools={"request_appointment": {"error": "ese horario ya no está"}})
        self.assertEqual(llamadas, ["request_appointment"])
        self.assertEqual(out["texto"], i18n.t_or("assistant.action_not_done", "\0", "es"))

    def test_refused_cancellation(self):
        cancel = {"tool_calls": [{"id": "c-1", "function": {
            "name": "cancel_request", "arguments": '{"request_id": 7}'}}]}
        out, _, _ = _turn(USER, [cancel, {"content": "Listo, el turno fue cancelado."}],
                          tools={"cancel_request": {"error": "no encontrada"}})
        self.assertEqual(out["texto"], i18n.t_or("assistant.action_not_done", "\0", "es"))

    def test_honest_explanation_after_a_refusal_stays(self):
        honest = "Ese horario ya no está. ¿Querés el de las 10:00?"
        out, _, _ = _turn(USER, [BOOK, {"content": honest}],
                          tools={"request_appointment": {"error": "ese horario ya no está"}})
        self.assertEqual(out["texto"], honest)

    def test_reading_an_approved_appointment_is_not_an_action(self):
        # my_requests is a read: «quedó confirmado» about an appointment the
        # desk already approved is the domain's own state, not a claim.
        texto = "Tu turno del lunes quedó confirmado por el hospital."
        out, _, _ = _turn(USER, [LIST, {"content": texto}], texto="¿Cómo está mi turno?",
                          tools={"my_requests": {"solicitudes": [{"estado": "aprobada"}]}})
        self.assertEqual(out["texto"], texto)

    def test_success_claims_in_every_language(self):
        refused = [("request_appointment", {"error": "x"})]
        for claim in (CLAIM, "Ya te reservé el turno.", "Your appointment is confirmed.",
                      "I've booked it for Monday.", "Sua consulta foi agendada.",
                      "L'appuntamento è stato prenotato.", "Votre rendez-vous est confirmé.",
                      "Ihr Termin wurde gebucht.", "\u0412\u0430\u0448\u0430 \u0437\u0430\u043f\u0438\u0441\u044c \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0430.",
                      "\u0412\u0430\u0448 \u0437\u0430\u043f\u0438\u0441 \u043f\u0456\u0434\u0442\u0432\u0435\u0440\u0434\u0436\u0435\u043d\u043e.", "予約が確定しました。", "您的预约已确认。"):
            with self.subTest(claim=claim):
                self.assertTrue(chat_agent._unbacked_action_claim(claim, refused))
        self.assertFalse(chat_agent._unbacked_action_claim(
            CLAIM, [("request_appointment", {"ok": True})]))


if __name__ == "__main__":
    unittest.main()
