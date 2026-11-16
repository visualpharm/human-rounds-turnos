"""test_chat_confirm_words.py — the chat books on the confirm word of the
patient's own language.

The proposal asks for the word in the patient's language (Russian, Japanese
«「確定する」と返信») but the transport only took confirmar /
confirmo / si confirmo / confirm / yes confirm, folded to ASCII — a Cyrillic
or CJK reply came out empty and the booking never happened. Now the word is
`ai.booking.confirm_word` of the proposal's language, the proposal prints that
same word through {word}, and the Spanish and English words stay accepted in
every language.

Run: python3 -m unittest discover -s tests -p "test_chat_confirm_words.py"
"""

import os
import sys
import unittest
from time import time
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import i18n  # noqa: E402
from app.modules.chat import chat_tools  # noqa: E402

_INTENT = {"service_id": "synthetic", "date": "2099-01-01", "time": "10:00"}


class PalabraDeConfirmacion(unittest.TestCase):
    def _reply(self, text, proposal_lang, active="es"):
        proposal = {"args": _INTENT, "patient_dni": "99000001", "created": time()}
        if proposal_lang:
            proposal["lang"] = proposal_lang
        token = i18n._ACTIVE.set(active)
        try:
            with patch.object(chat_tools.chat_store, "chat_metadata_get", return_value=dict(proposal)), \
                 patch.object(chat_tools.chat_store, "chat_take_booking_proposal", return_value=dict(proposal)), \
                 patch.object(chat_tools, "_tool_request", return_value={"ok": True}) as book:
                out = chat_tools.confirm_booking_reply({"dni": "99000001"}, text, "web", "k")
        finally:
            i18n._ACTIVE.reset(token)
        return out, book

    def assertBooks(self, text, lang, active="es"):
        out, book = self._reply(text, lang, active)
        self.assertEqual(out, {"ok": True}, f"{text!r} in {lang} did not book")
        self.assertEqual(book.call_args.args[2].confirmed_booking, _INTENT)

    def assertDoesNotBook(self, text, lang):
        out, book = self._reply(text, lang)
        self.assertIsNone(out)
        book.assert_not_called()

    def test_ruso_con_su_palabra(self):
        word = i18n.t_or("ai.booking.confirm_word", "", "ru")
        self.assertNotEqual(word, "Confirmar")
        for text in (word, word.lower(), f"  {word.upper()} ", f"«{word}»", f"{word}!"):
            with self.subTest(text=text):
                self.assertBooks(text, "ru")

    def test_japones_con_su_palabra(self):
        word = i18n.t_or("ai.booking.confirm_word", "", "ja")
        self.assertNotEqual(word, "Confirmar")
        for text in (word, f"「{word}」", f" {word}。"):
            with self.subTest(text=text):
                self.assertBooks(text, "ja")

    def test_castellano_conserva_las_palabras_de_siempre(self):
        for text in ("Confirmar", "confirmo", "Sí confirmo", "Si confirmo", "CONFIRM", "yes confirm", "Confirmar."):
            with self.subTest(text=text):
                self.assertBooks(text, "es")

    def test_castellano_e_ingles_valen_en_cualquier_idioma(self):
        for lang in ("ru", "ja", "zh", "de"):
            for text in ("Confirmar", "confirm"):
                with self.subTest(lang=lang, text=text):
                    self.assertBooks(text, lang)

    def test_propuesta_sin_idioma_usa_el_activo(self):
        self.assertBooks(i18n.t_or("ai.booking.confirm_word", "", "de"), None, active="de")

    def test_otra_respuesta_no_reserva(self):
        ru_word = i18n.t_or("ai.booking.confirm_word", "", "ru")
        # Russian «no», «yes» and «tomorrow», as escapes (test_no_cyrillic).
        no_ru = "\u043d\u0435\u0442"
        for text in ("\u0434\u0430", ru_word + " \u0437\u0430\u0432\u0442\u0440\u0430", "はい", "", "ok",
                     # a Latin word plus a refusal in another script, or a refusing emoji
                     "confirm " + no_ru, "Confirmar 不要", "Confirmar ❌", ru_word + " 👎"):
            with self.subTest(text=text):
                self.assertDoesNotBook(text, "ru")

    def test_la_conversacion_manda_sobre_la_pagina(self):
        # Proposal stored without its language: the conversation's (passed by
        # chat_agent) wins over the page's active language.
        word = i18n.t_or("ai.booking.confirm_word", "", "ja")
        proposal = {"args": _INTENT, "patient_dni": "99000001", "created": time()}
        with patch.object(chat_tools.chat_store, "chat_metadata_get", return_value=dict(proposal)), \
             patch.object(chat_tools.chat_store, "chat_take_booking_proposal", return_value=dict(proposal)), \
             patch.object(chat_tools, "_tool_request", return_value={"ok": True}):
            out = chat_tools.confirm_booking_reply({"dni": "99000001"}, word, "web", "k", "ja")
        self.assertEqual(out, {"ok": True})

    def test_la_propuesta_pide_la_misma_palabra_que_se_acepta(self):
        service = {"id": "synthetic", "nombre": "Cardiología", "modo": "solicitud"}
        for lang in ("es", "en", "ru", "uk", "ja", "zh", "de", "fr", "it", "pt"):
            # The page is Spanish; the conversation's language comes with the call.
            token = i18n._ACTIVE.set("es")
            try:
                with self.subTest(lang=lang), \
                     patch("app.patient_web.site_catalog._service_by_id", return_value=service), \
                     patch("app.patient_web.site_availability._offers_appointments", return_value=True), \
                     patch.object(chat_tools, "_booking_order_error", return_value=None), \
                     patch.object(chat_tools, "_base_url", return_value="https://synthetic.example"), \
                     patch.object(chat_tools.chat_store, "get_user",
                                  return_value={"nombre": "Sofía", "apellido": "Herrera"}), \
                     patch.object(chat_tools.chat_store, "chat_save_message") as save:
                    note = chat_tools._tool_request(
                        {"dni": "99000001"}, _INTENT,
                        chat_tools.ToolInvocation(channel="web", conversation_key="k", language=lang))["nota"]
                    word = i18n.t_or("ai.booking.confirm_word", "", lang)
                    self.assertTrue(word)
                    self.assertIn(word, note)
                    self.assertNotIn("{word}", note)
                    if lang == "en":  # the date too is in the conversation's language
                        self.assertIn("January", note)
                    self.assertEqual(i18n.active(), "es")
                    self.assertEqual(save.call_args.args[2]["lang"], lang)
            finally:
                i18n._ACTIVE.reset(token)


if __name__ == "__main__":
    unittest.main()
