"""tests/routes/test_body_contract_clock.py — the body-contract batteries do not
depend on the day they run (TASK-24).

The legal texts answer `actualizado` = the day of the edit, taken from the
Postgres clock, so the golden files cannot hold a calendar date: a battery
recorded on 2026-10-26 went red from 2026-10-27 on. `_body_contract.normalize`
masks that one field; this file pins that the mask is there for ANY day and
that it hides nothing else: a changed shape of the answer is still red.

No database: the golden file is read, its mask is replaced by a given day,
and the normalizer must give the golden text back.
"""

import json
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _body_contract as bc  # noqa: E402

_KEY = "PUT /api/admin/legal/terminos"
_DAYS = ("2026-10-26", "2026-10-27", "2026-11-30", "2027-01-01", "2028-02-29")


def _golden_body():
    answers = bc.Golden("admin_settings").section(_KEY)
    return answers["valid"]["body"]


class TestBodyContractClock(unittest.TestCase):

    def test_golden_holds_a_mask_not_a_day(self):
        text = _golden_body()
        self.assertIn('"actualizado": "<date>"', text)
        for name, got in bc.Golden("admin_settings").section(_KEY).items():
            self.assertNotRegex(got["body"], r'"actualizado": "\d{4}-\d\d-\d\d"', name)

    def test_any_day_gives_the_golden_text(self):
        golden = _golden_body()
        for day in _DAYS:
            with self.subTest(day=day):
                live = golden.replace('"actualizado": "<date>"', f'"actualizado": "{day}"')
                self.assertNotEqual(live, golden)
                self.assertEqual(bc.normalize(live), golden)

    def test_every_pinned_answer_survives_any_day(self):
        for name, got in bc.Golden("admin_settings").section(_KEY).items():
            for day in _DAYS:
                with self.subTest(case=name, day=day):
                    live = got["body"].replace('"actualizado": "<date>"', f'"actualizado": "{day}"')
                    self.assertEqual(bc.normalize(live), got["body"])

    def test_a_changed_shape_is_still_red(self):
        golden = _golden_body()
        live = json.loads(golden.replace("<date>", "2026-11-30"))
        item = live["item"]
        mutations = {
            "key renamed": lambda: (item.pop("actualizado"), item.update(updated="2026-11-30")),
            "key dropped": lambda: item.pop("actualizado"),
            "now null": lambda: item.update(actualizado=None),
            "now a number": lambda: item.update(actualizado=20261130),
            "date format changed": lambda: item.update(actualizado="30/11/2026"),
            "date became a timestamp": lambda: item.update(actualizado="2026-11-30T10:00:00Z"),
            "field added": lambda: item.update(extra=1),
            "other field changed": lambda: item.update(editado=False),
        }
        base = json.dumps(live, ensure_ascii=False)
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                changed = json.loads(base)
                item = changed["item"]
                mutate()
                self.assertNotEqual(bc.normalize(json.dumps(changed, ensure_ascii=False)), golden)

    def test_fixed_dates_stay_exact(self):
        text = json.dumps({"nacimiento": "1990-01-02", "actualizado": "2026-11-30"})
        self.assertEqual(json.loads(bc.normalize(text)),
                         {"nacimiento": "1990-01-02", "actualizado": "<date>"})
        self.assertTrue(re.search(r"1990-01-02", bc.normalize(text)))


if __name__ == "__main__":
    unittest.main()
