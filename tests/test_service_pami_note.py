"""test_service_pami_note.py — whom the PAMI note on a service page speaks to
(hr-patient review, 2026-11-15).

No database needed: `_pami_applies` is a pure function over the session's
coverage jsonb.
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.patient_web import site_page_service  # noqa: E402


def _user(*nombres, prepaga=None):
    cob = {"obras_sociales": [{"nombre": n} for n in nombres]}
    if prepaga:
        cob["prepaga"] = {"nombre": prepaga}
    return {"dni": "30111222", "cobertura": cob}


class PamiNoteTestCase(unittest.TestCase):

    def test_anonymous_or_unknown_coverage_sees_it(self):
        self.assertTrue(site_page_service._pami_applies(None))
        self.assertTrue(site_page_service._pami_applies({"dni": "1", "cobertura": {}}))

    def test_ioma_only_does_not_see_it(self):
        self.assertFalse(site_page_service._pami_applies(_user("O.S.P. BUENOS AIRES (I.O.M.A.)")))

    def test_every_registry_spelling_of_pami_sees_it(self):
        for nombre in ("PAMI", "INSSJP",
                       "INSTITUTO NACIONAL DE SERVICIOS SOCIALES PARA JUBILADOS Y PENSIONADOS",
                       "I.N.S.S.J.P."):
            with self.subTest(nombre=nombre):
                self.assertTrue(site_page_service._pami_applies(_user("IOMA", nombre)))

    def test_a_word_that_merely_contains_pami_does_not(self):
        self.assertFalse(site_page_service._pami_applies(_user("SAPAMIX SALUD")))

    def test_the_old_single_shape_counts(self):
        self.assertTrue(site_page_service._pami_applies(
            {"dni": "1", "cobertura": {"obra_social": "PAMI"}}))
        self.assertFalse(site_page_service._pami_applies(
            {"dni": "1", "cobertura": {"obra_social": "OSDE"}}))


if __name__ == "__main__":
    unittest.main()
