"""test_i18n_plural.py — core/i18n.py plural_category()/tn_or(), the server
twin of static/platform/i18n.mjs's pluralCategory()/tn() (05.11: «1
solicitudes», «1 libres» on the panel). Both read the same table,
tests/fixtures/plural-one.json, so the two halves cannot drift.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_i18n_plural.py
"""

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import _env  # noqa: E402

_env.process_defaults(TURNOS_FAKE_AI="1", TURNOS_SKIP_HSI="1")

from app.core import i18n  # noqa: E402

with open(os.path.join(ROOT, "tests", "fixtures", "plural-one.json"), encoding="utf-8") as fh:
    TABLE = json.load(fh)


class PluralCategoryTest(unittest.TestCase):
    def test_shared_table(self):
        # Russian and Ukrainian category by category; every other language
        # folds into one/other (no catalog there carries _few/_many).
        for lang, ones in TABLE["one"].items():
            few = TABLE.get("few", {}).get(lang)
            many = TABLE.get("many", {}).get(lang, [])
            for n in TABLE["numbers"]:
                with self.subTest(lang=lang, n=n):
                    if few is not None:
                        want = ("one" if n in ones else "few" if n in few
                                else "many" if n in many else "other")
                    else:
                        want = "one" if n in ones else "other"
                    self.assertEqual(i18n.plural_category(n, lang), want)

    def test_formatted_and_non_numbers(self):
        self.assertEqual(i18n.plural_category("1", "es"), "one")
        self.assertEqual(i18n.plural_category("1,0", "es"), "one")
        self.assertEqual(i18n.plural_category("1,5", "es"), "other")
        self.assertEqual(i18n.plural_category(None, "es"), "other")
        self.assertEqual(i18n.plural_category("—", "es"), "other")
        self.assertEqual(i18n.plural_category(True, "es"), "other")

    def test_default_language_is_spanish(self):
        self.assertEqual(i18n.plural_category(1), "one")
        self.assertEqual(i18n.plural_category(21), "other")


class TnOrTest(unittest.TestCase):
    def test_spanish_fallbacks(self):
        self.assertEqual(i18n.tn_or("no.such.key", 1, "{n} turno", "{n} turnos", "es"), "1 turno")
        self.assertEqual(i18n.tn_or("no.such.key", 3, "{n} turno", "{n} turnos", "es"), "3 turnos")

    def test_catalog_keys(self):
        key = "admin.system.status.or.days"
        self.assertEqual(i18n.tn_or(key, "1", "~{n} día", "~{n} días", "es"), "~1 día")
        self.assertEqual(i18n.tn_or(key, "4", "~{n} día", "~{n} días", "es"), "~4 días")
        self.assertEqual(i18n.tn_or(key, "1", "~{n} día", "~{n} días", "en"), "~1 day")

    def test_other_vars_and_formatted_n(self):
        text = i18n.tn_or("no.such.key", 1, "{nombre}: {n} solicitud", "{nombre}: {n} solicitudes", "es", nombre="Clínica")
        self.assertEqual(text, "Clínica: 1 solicitud")
        text = i18n.tn_or("no.such.key", 1200, "{n} turno", "{n} turnos", "es", n="1.200")
        self.assertEqual(text, "1.200 turnos")

    def test_template_variable_named_like_a_parameter(self):
        # «Registro IA» in Sistema passes count=: a template variable may share
        # a parameter's name without colliding with it.
        self.assertEqual(i18n.tn_or("no.such.key", 1, "{count} aviso", "{count} avisos", "es", count=1), "1 aviso")
        self.assertEqual(i18n.tn_or("ai.register.system_count", 3, "{count} notificación pendiente",
                                    "{count} notificaciones pendientes", "es", count=3), "3 notificaciones pendientes")

    def test_whole_phrase_only_for_exactly_one(self):
        one, other = "¿Aprobar la solicitud?", "¿Aprobar las {n} solicitudes?"
        self.assertEqual(i18n.tn_or("no.such.key", 1, one, other, "es"), one)
        # French "one" covers 0, Russian "one" covers 21: a phrase with no
        # number in it would lie about them, so they take the plural.
        self.assertEqual(i18n.tn_or("no.such.key", 0, one, other, "fr"), "¿Aprobar las 0 solicitudes?")
        self.assertEqual(i18n.tn_or("no.such.key", 21, one, other, "ru"), "¿Aprobar las 21 solicitudes?")
        one_ru, other_ru = "{n} \u0437\u0430\u044f\u0432\u043a\u0430", "{n} \u0437\u0430\u044f\u0432\u043e\u043a"
        self.assertEqual(i18n.tn_or("no.such.key", 21, one_ru, other_ru, "ru"), one_ru.replace("{n}", "21"))


    def test_russian_and_ukrainian_few_and_many(self):
        # 2 and 5 days, 21 days: the derived keys of
        # scripts/translate_locales.py, read through the real catalogs.
        key = "admin.queue.solicitudes.n_dia"
        for lang in ("ru", "uk"):
            with self.subTest(lang=lang):
                few, many, one = (i18n.raw(key + s, lang) for s in ("_few", "_many", "_one"))
                self.assertNotEqual(few, key + "_few")
                self.assertNotEqual(many, key + "_many")
                self.assertNotEqual(few, many)
                for n, form in ((2, few), (4, few), (22, few), (5, many), (11, many),
                                (12, many), (0, many), (21, one)):
                    self.assertEqual(i18n.tn_or(key, n, "{n} d\u00eda", "{n} d\u00edas", lang),
                                     form.replace("{n}", str(n)))

    def test_a_bare_unit_word_serves_every_one_count(self):
        # «21 médico» in Russian is the singular word (2026-11-06: the demo
        # printed the plural after 21); a whole phrase still waits for 1.
        one_ru, other_ru = "vrach", "vrachi"
        self.assertEqual(i18n.tn_or("no.such.key", 21, one_ru, other_ru, "ru"), one_ru)
        self.assertEqual(i18n.tn_or("no.such.key", 31, one_ru, other_ru, "uk"), one_ru)
        self.assertEqual(i18n.tn_or("no.such.key", 1, "médico", "médicos", "es"), "médico")
        self.assertEqual(i18n.tn_or("no.such.key", 0, "médecin", "médecins", "fr"), "médecin")
        self.assertEqual(i18n.tn_or("no.such.key", 21, "La solicitud.", "Las solicitudes.", "ru"), "Las solicitudes.")

    def test_spanish_singulars_carry_their_number(self):
        # A Spanish `_one` that wrote «1» instead of «{n}» was translated with
        # the 1 in it, so Russian 21 fell to the plural (2026-11-06).
        import re
        lit = re.compile(r"(?<![\d.,{])1(?![\d.,}])")
        d = os.path.join(ROOT, "locales", "es")
        for f in sorted(os.listdir(d)):
            if not f.endswith(".json") or f.startswith("_"):
                continue
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                for key, text in json.load(fh).items():
                    if key.endswith("_one") and isinstance(text, str):
                        with self.subTest(key=key):
                            self.assertNotRegex(text, lit)

    def test_real_catalogs_for_1_2_5_21_22_25(self):
        key = "admin.content.catalog.doctors.import_created"
        for lang in ("ru", "uk"):
            forms = {s: i18n.raw(key + s, lang) for s in ("_one", "_few", "_many")}
            for n, form in ((1, "_one"), (2, "_few"), (5, "_many"), (21, "_one"), (22, "_few"), (25, "_many")):
                with self.subTest(lang=lang, n=n):
                    got = i18n.tn_or(key, n, "{n} médico creado", "{n} médicos creados", lang)
                    self.assertEqual(got, forms[form].replace("{n}", str(n)))
                    self.assertIn(str(n), got)

    def test_few_and_many_only_where_the_catalog_has_them(self):
        # Spanish «many» of a million and a Russian key without derived forms
        # stay on the plural key.
        self.assertEqual(i18n.tn_or("no.such.key", 1000000, "{n} turno", "{n} turnos", "es"), "1.000.000 turnos")
        self.assertEqual(i18n.tn_or("no.such.key", 3, "{n} turno", "{n} turnos", "ru"), "3 turnos")


if __name__ == "__main__":
    unittest.main()
