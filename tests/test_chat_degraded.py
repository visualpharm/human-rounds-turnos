"""test_chat_degraded.py — what the patient reads when the assistant is down.

QA 2026-10-25 (codex-pinamar-chat-qa-20261025, source e8b70d7): «Tengo dolor
de pecho y dificultad para respirar ahora. ¿Qué hago?» got the generic «el
asistente no está disponible… sacar turnos» whenever the model was down — no
emergency action at all. Five ways the assistant degrades, every one of them
pinned here for every shipped language:

- no AI key configured (ai_client unavailable);
- the shared circuit breaker open (ai_health unavailable — 402/5xx/timeouts);
- the conversation's daily message limit used up;
- the model call timing out;
- the model answering with nothing.

An urgent conversation must lead with the emergency action, its number read
from the installation (never from code), and keep doing so on later turns
(P17). A plain question in the same outage gets an honest «not available»
with working site links and the catalog's own published hours (P18).

No Postgres needed: history and persistence are an in-memory list.
"""

import json
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
from app.core import site_identity  # noqa: E402
from app.modules.chat import chat_agent  # noqa: E402
from app.modules.chat import chat_api  # noqa: E402

_GET = site_identity.get

# One red-flag message per shipped language (the same symptoms the QA used).
RED_FLAGS = {
    "es": "Tengo dolor de pecho y dificultad para respirar ahora. ¿Qué hago?",
    "en": "I have chest pain and trouble breathing right now. What should I do?",
    "pt": "Estou com dor no peito e falta de ar agora. O que eu faço?",
    "it": "Ho dolore al petto e mancanza di fiato adesso. Cosa devo fare?",
    "fr": "J'ai une douleur dans la poitrine et du mal à respirer. Que dois-je faire ?",
    "de": "Ich habe Brustschmerzen und Atemnot. Was soll ich tun?",
    "ru": "\u0423 \u043c\u0435\u043d\u044f \u0431\u043e\u043b\u044c \u0432 \u0433\u0440\u0443\u0434\u0438 \u0438 \u043e\u0434\u044b\u0448\u043a\u0430. \u0427\u0442\u043e \u043c\u043d\u0435 \u0434\u0435\u043b\u0430\u0442\u044c?",
    "uk": "\u0423 \u043c\u0435\u043d\u0435 \u0431\u0456\u043b\u044c \u0443 \u0433\u0440\u0443\u0434\u044f\u0445 \u0456 \u0437\u0430\u0434\u0438\u0448\u043a\u0430. \u0429\u043e \u043c\u0435\u043d\u0456 \u0440\u043e\u0431\u0438\u0442\u0438?",
    "ja": "胸の痛みと息苦しさがあります。どうすればいいですか？",
    "zh": "我现在胸痛，呼吸困难。我该怎么办？",
}

# The patient SAYING it is an emergency, with no symptom named (P17's first line).
DECLARED = {
    "es": "Es una emergencia médica. Necesito ayuda urgente. ¿Espero a que responda este chat?",
    "en": "This is a medical emergency. I need help urgently. Should I wait for this chat?",
    "pt": "É uma emergência médica. Preciso de ajuda urgente.",
    "it": "È un'emergenza medica. Ho bisogno di aiuto urgente.",
    "fr": "C'est une urgence médicale. J'ai besoin d'aide tout de suite.",
    "de": "Das ist ein medizinischer Notfall. Ich brauche dringend Hilfe.",
    "ru": "\u042d\u0442\u043e \u044d\u043a\u0441\u0442\u0440\u0435\u043d\u043d\u0430\u044f \u0441\u0438\u0442\u0443\u0430\u0446\u0438\u044f, \u0441\u0440\u043e\u0447\u043d\u043e \u043d\u0443\u0436\u043d\u0430 \u043f\u043e\u043c\u043e\u0449\u044c.",
    "uk": "\u0426\u0435 \u0435\u043a\u0441\u0442\u0440\u0435\u043d\u0430 \u0441\u0438\u0442\u0443\u0430\u0446\u0456\u044f, \u0442\u0435\u0440\u043c\u0456\u043d\u043e\u0432\u043e \u043f\u043e\u0442\u0440\u0456\u0431\u043d\u0430 \u0434\u043e\u043f\u043e\u043c\u043e\u0433\u0430.",
    "ja": "緊急です。すぐに助けが必要です。",
    "zh": "这是紧急情况，我需要马上帮助。",
}

CONDITIONS = ("no_key", "breaker_open", "daily_limit", "timeout", "empty")


def _emergency_line(lang, number="107"):
    if number:
        return i18n.t_or("assistant.emergency", "", lang, number=number)
    return i18n.t_or("assistant.emergency_no_number", "", lang)


class _Harness:
    """Runs real `_run_turn`/`chat_full` turns over an in-memory history with
    one of the five degradations switched on."""

    def __init__(self, condition, number="107"):
        self.condition = condition
        self.number = number
        self.history = []
        self.model_calls = 0

    def _model(self, *args, **kwargs):
        self.model_calls += 1
        if self.condition == "timeout":
            raise TimeoutError("read timed out")
        if self.condition == "empty":
            return {"choices": [{"message": {"content": ""}}]}
        if self.condition == "blank":
            return {"choices": [{"message": {"content": "  \n "}}]}
        if self.condition == "healthy":
            return {"choices": [{"message": {"content": "Respuesta del modelo."}}]}
        raise AssertionError(f"the model must not be called ({self.condition})")

    def _get(self, key, default=None):
        if key == "emergency_number":
            return self.number if self.number else (default if default is not None else "")
        return _GET(key, default)

    def _first(self, *_a):
        return next((m["content"] for m in self.history if m.get("role") == "user"), "")

    def turn(self, texto, full=False):
        key_ok = self.condition != "no_key"
        breaker_ok = self.condition != "breaker_open"
        used = 99 if self.condition == "daily_limit" else 1
        env = {"TURNOS_FAKE_CHAT_AI": "0", "TURNOS_FAKE_AI": "0"}
        with patch.object(chat_agent, "_call_chat", side_effect=self._model), \
             patch.object(chat_agent, "_save",
                          side_effect=lambda canal, clave, m: self.history.append(dict(m))), \
             patch.object(chat_agent, "_load_history",
                          side_effect=lambda *a: [dict(m) for m in self.history]), \
             patch.object(chat_agent, "_first_user_message", side_effect=self._first), \
             patch.object(chat_agent, "_turns_used", return_value=used), \
             patch.object(chat_agent, "_memory_block", return_value=""), \
             patch.object(chat_agent, "_env",
                          side_effect=lambda key, default="": env.get(key, default)), \
             patch.object(site_identity, "get", side_effect=self._get), \
             patch("app.ai.ai_client.available", return_value=key_ok), \
             patch("app.ai.ai_client.primary_key", return_value="test-key"), \
             patch("app.ai.ai_health.available", return_value=breaker_ok), \
             patch("app.ai.ai_health.report"):
            if full:
                return chat_agent.chat_full(None, texto, "test:degraded", "web")
            return chat_agent._run_turn(None, texto, "test:degraded", "web")


class TestEmergencyDetectorWithoutModel(unittest.TestCase):
    def test_red_flags_and_declarations_in_every_language(self):
        for lang, texto in list(RED_FLAGS.items()) + list(DECLARED.items()):
            with self.subTest(lang=lang, texto=texto):
                self.assertTrue(chat_agent._is_urgent(texto))

    def test_cannot_breathe(self):
        for texto in ("I can't breathe", "I can’t breathe, help",
                      "I cannot breathe", "No puedo respirar",
                      "I have shortness of breath"):
            with self.subTest(texto=texto):
                self.assertTrue(chat_agent._is_urgent(texto))

    def test_schedule_questions_are_not_emergencies(self):
        for texto in (
                "Quiero consultar horarios de dermatología, sin reservar.",
                "¿Ya consultaste los horarios?",
                "¿Qué días atiende pediatría?",
                "What are the opening hours of the clinic?",
                "Hola, quiero un turno con el cardiólogo.",
        ):
            with self.subTest(texto=texto):
                self.assertFalse(chat_agent._is_urgent(texto))


class TestEmergencyFirstWhenDegraded(unittest.TestCase):
    def test_every_condition_every_language_leads_with_the_emergency_action(self):
        for condition in CONDITIONS:
            for lang, texto in RED_FLAGS.items():
                with self.subTest(condition=condition, lang=lang):
                    h = _Harness(condition)
                    out = h.turn(texto)
                    line = _emergency_line(lang)
                    self.assertTrue(line, f"assistant.emergency missing in {lang}")
                    self.assertIn("107", line)
                    self.assertTrue(out["texto"].startswith(line), out["texto"])
                    if lang != "es":
                        self.assertNotEqual(
                            line, _emergency_line("es"),
                            f"{lang} falls back to Spanish")
                    # Never «try again later» / «rephrase» to someone in an
                    # emergency: the canned replies that ask them to wait.
                    for waits in ("assistant.error", "assistant.rephrase",
                                  "assistant.too_many_steps"):
                        self.assertNotIn(i18n.t_or(waits, "\0", lang), out["texto"])
                    # The emergency line is what gets persisted, so the next
                    # turn's history shows it was given.
                    self.assertEqual(h.history[-1]["content"], out["texto"])

    def test_declared_emergency_without_symptoms(self):
        for condition in CONDITIONS:
            with self.subTest(condition=condition):
                out = _Harness(condition).turn(DECLARED["es"])
                self.assertTrue(out["texto"].startswith(_emergency_line("es")))

    def test_number_comes_from_the_installation(self):
        out = _Harness("no_key", number="911").turn(RED_FLAGS["es"])
        self.assertIn("911", out["texto"])
        self.assertNotIn("107", out["texto"])

    def test_installation_without_number_gets_the_neutral_line(self):
        for condition in CONDITIONS:
            with self.subTest(condition=condition):
                out = _Harness(condition, number="").turn(RED_FLAGS["es"])
                line = _emergency_line("es", number="")
                self.assertTrue(line)
                self.assertTrue(out["texto"].startswith(line), out["texto"])
                self.assertNotRegex(out["texto"], r"\d{3}")

    def test_urgency_persists_across_later_turns_p17(self):
        # P17: the first line declares the emergency, the next two only ask
        # whether to keep waiting — both still get the emergency action.
        for condition in CONDITIONS:
            with self.subTest(condition=condition):
                h = _Harness(condition)
                for texto in (
                        "Es una emergencia médica. Necesito ayuda urgente. ¿Espero a que responda este chat?",
                        "No responde el asistente. ¿Me quedo esperando acá?",
                        "¿Hola?",
                ):
                    out = h.turn(texto)
                    self.assertTrue(out["texto"].startswith(_emergency_line("es")),
                                    (texto, out["texto"]))

    def test_recovery_hands_the_turn_back_to_the_model(self):
        h = _Harness("no_key")
        h.turn(RED_FLAGS["es"])
        h.condition = "healthy"
        out = h.turn("¿Y ahora?")
        self.assertEqual(out["texto"], "Respuesta del modelo.")
        self.assertEqual(h.model_calls, 1)

    def test_blank_model_answer_is_a_failed_turn(self):
        out = _Harness("blank").turn(RED_FLAGS["es"])
        self.assertTrue(out["texto"].startswith(_emergency_line("es")), out["texto"])
        plain = _Harness("blank").turn("¿Qué días atiende pediatría?")
        self.assertEqual(plain["texto"],
                         i18n.t_or("assistant.rephrase", "¿Podés repetirlo de otra forma?", "es"))

    def test_off_topic_after_an_emergency_keeps_the_scope_reply(self):
        # A healthy model still never sees an off-topic question; after an
        # emergency in the session the scope reply leads with the action.
        h = _Harness("no_key")
        h.turn(RED_FLAGS["es"])
        h.condition = "healthy"
        out = h.turn("Pasame una receta de cocina")
        self.assertEqual(h.model_calls, 0)
        self.assertTrue(out["texto"].startswith(_emergency_line("es")), out["texto"])
        self.assertIn(i18n.t_or("assistant.out_of_scope", "\0", "es",
                                network=_GET("network", None)), out["texto"])


class TestPlainQuestionWhenDegraded(unittest.TestCase):
    def test_schedule_question_gets_honest_outage_and_navigation(self):
        for condition in ("no_key", "breaker_open", "timeout"):
            with self.subTest(condition=condition):
                out = _Harness(condition).turn(
                    "Quiero consultar horarios de dermatología, sin reservar.", full=True)
                self.assertNotIn(_emergency_line("es"), out["texto"])
                self.assertIn("Por ahora no podemos responderte por chat", out["texto"])
                self.assertIn("sacá tu turno en", out["texto"])
                hrefs = {e["href"] for e in out["enlaces"]}
                self.assertIn("/centers", hrefs)
                self.assertIn("/", hrefs)

    def test_catalog_hours_without_the_model_p18(self):
        # P18 on the public example-town pack: Dermatología is one service with
        # one published line of hours, read here from the pack itself (the demo
        # catalog moves its agendas). The follow-up asks whether the hours
        # were checked — the reply says it could not, and still carries the
        # published hours from the earlier message, with the service linked.
        with open(os.path.join(ROOT, "packs", "example-town", "servicios.json"),
                  encoding="utf-8") as fh:
            hours = [s["horario"] for s in json.load(fh)["servicios"]
                     if s["id"] == "dermatologia-hospital"]
        self.assertEqual(len(hours), 1)
        self.assertRegex(hours[0], r"\d+ a \d+ h")
        h = _Harness("no_key")
        first = h.turn("Quiero consultar horarios de dermatología, sin reservar.", full=True)
        second = h.turn("¿Ya consultaste los horarios?", full=True)
        for out in (first, second):
            self.assertIn("Por ahora no podemos responderte por chat", out["texto"])
            self.assertIn(hours[0], out["texto"])
            self.assertIn("/service/dermatologia-hospital",
                          {e["href"] for e in out["enlaces"]})

    def test_outage_never_offers_whatsapp_or_a_later_answer(self):
        out = _Harness("no_key").turn("¿Qué días atiende pediatría?")
        lowered = out["texto"].lower()
        for promise in ("whatsapp", "te respondemos", "más tarde", "en un rato"):
            self.assertNotIn(promise, lowered)

    def test_daily_limit_keeps_its_own_message(self):
        out = _Harness("daily_limit").turn("¿Qué días atiende pediatría?")
        self.assertTrue(out["texto"].startswith("Llegaste al límite de mensajes"),
                        out["texto"])


class TestRateLimitedEmergency(unittest.TestCase):
    def test_429_still_carries_the_emergency_action(self):
        turn = chat_api.ChatTurnInput(usuario=None, ip="203.0.113.9",
                                      body={"mensaje": RED_FLAGS["es"]})
        with patch.object(chat_api, "_allowed", return_value=False), \
             patch.object(site_identity, "get",
                          side_effect=lambda k, d=None: "107" if k == "emergency_number" else _GET(k, d)):
            status, salida = chat_api.compute_output(turn)
        self.assertEqual(status, 429)
        self.assertTrue(salida["mensaje"].startswith(_emergency_line("es")))

    def test_429_speaks_the_language_the_patient_wrote_in(self):
        turn = chat_api.ChatTurnInput(usuario=None, ip="203.0.113.9",
                                      body={"mensaje": RED_FLAGS["en"]})
        with patch.object(chat_api, "_allowed", return_value=False), \
             patch.object(site_identity, "get",
                          side_effect=lambda k, d=None: "107" if k == "emergency_number" else _GET(k, d)):
            status, salida = chat_api.compute_output(turn)
        self.assertEqual(status, 429)
        self.assertTrue(salida["mensaje"].startswith(_emergency_line("en")))
        self.assertIn(i18n.t_or("assistant.rate_limited", "", "en"), salida["mensaje"])

    def test_429_plain_message_unchanged(self):
        turn = chat_api.ChatTurnInput(usuario=None, ip="203.0.113.9",
                                      body={"mensaje": "¿Qué días atiende pediatría?"})
        with patch.object(chat_api, "_allowed", return_value=False):
            status, salida = chat_api.compute_output(turn)
        self.assertEqual(status, 429)
        self.assertNotIn("107", salida["mensaje"])


if __name__ == "__main__":
    unittest.main()
