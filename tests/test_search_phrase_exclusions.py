"""A phrase in `palabras_excluidas` hides a row only when the query says the
whole phrase (contraparte técnica, 2026-10-19: «puse fiebre, tos y telemedicina no aparecía
como primera»). Same rule as static/platform/search.mjs, pinned for the Python
mirrors: the shared helper, the remedies matcher and the chat search."""

import unittest
from unittest import mock

from app.core import search_exclusions
from app.modules.turnos import remedies

_EXC = ["fiebre con confusión", "tose sangre", "dolor de pecho", "pap"]


def _norm_all(items):
    return [remedies._norm(x) for x in items]


class HelperTest(unittest.TestCase):
    def _hidden(self, q):
        words = [w for w in remedies._norm(q).replace(",", " ").split() if w]
        return search_exclusions.is_excluded(remedies._norm(q), words, _norm_all(_EXC))

    def test_single_word_starting_a_phrase_is_not_hidden(self):
        for q in ("fiebre", "tos", "fiebre, tos", "fiebre tos", "dolor de cabeza"):
            self.assertFalse(self._hidden(q), q)

    def test_whole_phrase_hides(self):
        for q in ("fiebre con confusión", "mi hijo tose sangre", "dolor de pecho fuerte"):
            self.assertTrue(self._hidden(q), q)

    def test_single_word_exclusion_keeps_prefix_both_ways(self):
        self.assertTrue(self._hidden("pap"))
        self.assertTrue(self._hidden("papanicolaou"))
        self.assertFalse(self._hidden("pa"))


class RemediesTest(unittest.TestCase):
    def test_score_keeps_row_for_symptom_word(self):
        campos = remedies._service_fields({
            "nombre": "Telemedicina", "palabras": ["fiebre", "tos"],
            "palabras_excluidas": _EXC})
        self.assertIsNotNone(remedies._score(campos, "fiebre"))
        self.assertIsNotNone(remedies._score(campos, "fiebre, tos"))
        self.assertIsNone(remedies._score(campos, "fiebre con confusión"))


class ChatSearchTest(unittest.TestCase):
    def test_chat_search_keeps_service_for_symptom_word(self):
        from app.modules.chat import chat_tools
        servicio = {"id": "tele", "nombre": "Telemedicina", "palabras": ["fiebre", "tos"],
                    "palabras_excluidas": _EXC, "centro": "hospital"}
        with mock.patch("app.patient_web.catalog.services", return_value=[servicio]), \
                mock.patch.object(chat_tools, "_centros_nombre", return_value={}), \
                mock.patch.object(chat_tools, "_idioma_activo", return_value="es"), \
                mock.patch.object(chat_tools, "_info_servicio",
                                  side_effect=lambda s, *a, **k: {"id": s.get("id")}):
            def ids(q):
                out = chat_tools._tool_search(None, {"query": q}, None)
                return [s.get("id") for s in out.get("resultados") or []]
            self.assertIn("tele", ids("fiebre"))
            self.assertNotIn("tele", ids("fiebre con confusión"))


if __name__ == "__main__":
    unittest.main()
