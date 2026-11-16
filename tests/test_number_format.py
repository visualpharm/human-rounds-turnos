"""The Python twin of the panel's number formatter writes every case of
tests/fixtures/number-format.json exactly as i18n.mjs does
(tests/number-format.test.mjs reads the same file)."""
import json
import os
import unittest

from app.core import format as fmt
from app.core import i18n

_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "number-format.json")


class NumberFormatParity(unittest.TestCase):
    def setUp(self):
        with open(_FIXTURE, encoding="utf-8") as fh:
            self.cases = json.load(fh)

    def test_number_cases(self):
        for c in self.cases["number"]:
            with self.subTest(c=c):
                self.assertEqual(fmt.number(c["n"], c["d"], c["lang"]), c["out"])

    def test_percent_cases(self):
        for c in self.cases["percent"]:
            with self.subTest(c=c):
                self.assertEqual(fmt.percent(c["n"], c["lang"]), c["out"])

    def test_empty_stays_empty(self):
        self.assertEqual(fmt.number(None), "")
        self.assertEqual(fmt.percent(""), "")
        self.assertEqual(fmt.number(True), "")

    def test_tn_or_groups_its_count(self):
        text = i18n.tn_or("admin.srv.test.nonexistent_count", 4188,
                          "{n} turno", "{n} turnos", "es")
        self.assertEqual(text, "4.188 turnos")


if __name__ == "__main__":
    unittest.main()
