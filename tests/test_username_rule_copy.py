"""What the screens say about a username is what core/usernames.py checks.

The installer's hint kept the pre-2026-09-05 rule ("3 to 40 characters:
letters, numbers, period, hyphen or underscore") while its own error and the
server already accepted any 2+ visible characters without spaces — and the
panel's Users screen repeated the same stale "3 to 40". One rule, so every
text about it names the same minimum, no maximum the server doesn't
enforce, no alphabet it doesn't check, and the browser copy of the rule in
admin-users.mjs carries the same numbers.

Run: TURNOS_FAKE_AI=1 python3 -m unittest discover -s tests -p "test_username_rule_copy.py"
"""
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.core import usernames  # noqa: E402

TEXTS = {
    "setup": ["setup.admin.username_hint", "setup.admin.error_username"],
    "admin.system": ["admin.system.users.username_format", "admin.system.users.username_short"],
}


def _catalog(lang, fname):
    with open(os.path.join(ROOT, "locales", lang, fname + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


class TestUsernameRuleCopy(unittest.TestCase):
    def test_every_text_names_the_server_minimum_and_nothing_else(self):
        for lang in ("es", "en"):
            for fname, keys in TEXTS.items():
                cat = _catalog(lang, fname)
                for key in keys:
                    with self.subTest(lang=lang, key=key):
                        self.assertEqual(re.findall(r"\d+", cat[key]), [str(usernames.MIN)], cat[key])

    def test_the_rule_texts_mention_spaces_not_an_alphabet(self):
        for lang, word in (("es", "espacio"), ("en", "space")):
            for fname, key in (("setup", "setup.admin.username_hint"),
                               ("admin.system", "admin.system.users.username_format")):
                with self.subTest(lang=lang, key=key):
                    text = _catalog(lang, fname)[key].lower()
                    self.assertIn(word, text)
                    self.assertNotRegex(text, r"guion|hyphen|underscore|punto|period")

    def test_the_browser_copy_of_the_rule_has_the_same_numbers(self):
        with open(os.path.join(ROOT, "static", "platform", "admin-users.mjs"), encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("var USERNAME_MIN = %d;" % usernames.MIN, src)
        self.assertIn("var USERNAME_MAX = %d;" % usernames.MAX, src)
        self.assertNotIn("de 3 a 40", src)


if __name__ == "__main__":
    unittest.main()
