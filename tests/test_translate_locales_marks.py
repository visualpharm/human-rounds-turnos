"""The opening «¿» and «¡» are Spanish only (2026-11-06): the model kept them
from the source in three generated strings (two Russian questions of
Solicitudes, French «¿Revenir au logo…»). scripts/translate_locales.py cleans every value
on its way into a non-Spanish catalog, the ones kept from earlier runs too,
and no generated catalog carries them.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_translate_locales_marks.py
"""
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import translate_locales as tl  # noqa: E402
from app.core import i18n  # noqa: E402

GENERATED = [c for c in i18n.LANGUAGE_NAMES if c not in ("es", "en")]


class TestSpanishMarks(unittest.TestCase):
    def test_cleaned_for_every_language_but_spanish(self):
        self.assertEqual(tl._no_spanish_marks("ru", "¿Ok? ¡Hecho!"), "Ok? Hecho!")
        self.assertEqual(tl._no_spanish_marks("fr", "¿Revenir au logo ?"), "Revenir au logo ?")
        self.assertEqual(tl._no_spanish_marks("es", "¿Aprobar?"), "¿Aprobar?")
        self.assertEqual(tl._no_spanish_marks("es-AR", "¡Listo!"), "¡Listo!")
        self.assertIsNone(tl._no_spanish_marks("ru", None))

    def test_a_fresh_translation_is_cleaned(self):
        items = {"k": {"es": "¿Aprobar la solicitud?", "en": "Approve the request?"}}
        with mock.patch.object(tl, "_call", return_value={"k": "¿Approuver la demande ?"}):
            got = tl.translate_items("fr", items)
        self.assertEqual(got, {"k": "Approuver la demande ?"})

    def test_no_generated_catalog_carries_them(self):
        for lang in GENERATED:
            d = os.path.join(ROOT, "locales", lang)
            for f in sorted(os.listdir(d)):
                if not f.endswith(".json") or f.startswith("_"):
                    continue
                with open(os.path.join(d, f), encoding="utf-8") as fh:
                    data = json.load(fh)
                for key, text in data.items():
                    if isinstance(text, str) and not key.startswith("_"):
                        with self.subTest(lang=lang, key=key):
                            self.assertNotRegex(text, "[¿¡]")


if __name__ == "__main__":
    unittest.main()
