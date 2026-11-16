"""scripts/translate_locales.py checks that a translation carries every tag
and placeholder of its source — but only REAL tags. "Remitente (nombre
<email>)" has none: German rightly says "Absender (Name <E-Mail>)", and the
old check read <email> as an HTML tag, rejected every attempt and left the
German panel in Spanish.

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_translate_locales_tags.py"
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import translate_locales as tl  # noqa: E402


class TestTagCheck(unittest.TestCase):
    def test_angle_brackets_around_a_word_are_prose(self):
        self.assertEqual(tl._placeholders("Remitente (nombre <email>)"), [])

    def test_real_tags_and_placeholders_still_count(self):
        self.assertEqual(tl._placeholders('Hola <b>{name}</b>, <a href="/x" title="ir">ver</a>'),
                         ["</a", "</b", "<a", "<b", "{name}"])

    def test_the_german_sender_label_is_accepted(self):
        items = {"admin.srv.settings.field.mail_from": {"es": "Remitente (nombre <email>)",
                                                        "en": "Sender (name <email>)"}}
        good, bad = tl._validate(items, {"admin.srv.settings.field.mail_from": "Absender (Name <E-Mail>)"})
        self.assertEqual(bad, [])
        self.assertEqual(good["admin.srv.settings.field.mail_from"], "Absender (Name <E-Mail>)")

    def test_a_dropped_tag_is_still_rejected(self):
        items = {"k": {"es": "Leé <b>esto</b>", "en": "Read <b>this</b>"}}
        good, bad = tl._validate(items, {"k": "Lies das"})
        self.assertEqual((good, bad), ({}, ["k"]))


if __name__ == "__main__":
    unittest.main()
