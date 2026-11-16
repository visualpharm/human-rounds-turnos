"""test_nav_section.py — which top-bar item a service's pages light up.

The product owner 26.10 (booking on salud): «Centros» was highlighted on Confirmá and on
«Solicitud enviada» — he asked why «Centros» at all. The wizard and the service
pages passed "centros" to `_shell` because their breadcrumb starts at the center.
The wizard is a process and lights nothing; a service page lights «Estudios»
for a study (the rule of its cluster page) and nothing otherwise.

Run: python3 scripts/turnos_managed_exec.py --profile backend-focused --test tests/test_nav_section.py
"""
import re
import unittest

from app.patient_web import site_availability, site_catalog, site_page_service


def _section(html):
    m = re.search(r'<header id="stznav" data-section="([^"]*)"', html)
    return m.group(1) if m else None


def _first(pred):
    for s in site_catalog._services():
        if pred(s):
            return s
    return None


class TestNavSection(unittest.TestCase):
    def test_wizard_lights_no_section(self):
        s = _first(site_availability._offers_appointments)
        self.assertIsNotNone(s, "the pack has no bookable service")
        page = site_page_service._page_service_appointment(s["id"])
        html = page.decode() if isinstance(page, bytes) else page.body.decode()
        self.assertEqual(_section(html), "")

    def test_service_page_is_never_centros(self):
        estudio = _first(lambda s: s.get("cluster") == "diagnostico")
        otro = _first(lambda s: s.get("cluster") not in (None, "", "diagnostico"))
        self.assertIsNotNone(estudio)
        self.assertIsNotNone(otro)
        self.assertEqual(_section(site_page_service._page_service(estudio["id"]).decode()), "estudios")
        self.assertEqual(_section(site_page_service._page_service(otro["id"]).decode()), "")


if __name__ == "__main__":
    unittest.main()
