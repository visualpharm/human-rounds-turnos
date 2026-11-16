"""Public health campaign pages and service-linked announcements."""

import re
import unittest
from unittest import mock
from pathlib import Path

from app.core import site_identity
from app.patient_web import site_base, site_news, site_preparation


class TestPublicCampaigns(unittest.TestCase):
    def test_home_uses_canonical_table_action_and_citizen_banners(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")
        listing = (root / "static" / "platform" / "listing.mjs").read_text(encoding="utf-8")
        self.assertNotIn("LINK_COLS", template)
        # Info links (and, since 2026-10-18, matching centers) are built rows
        # that go in front of the services in the same table.
        self.assertIn("var filasLnk = lnks.map(enlaceRow);", template)
        self.assertIn("lnks: filasLnk.concat(cens)", template)
        self.assertIn("rows: lnks.concat(svs.map(servRow))", template)
        self.assertIn('action: e.accion_texto', template)
        self.assertIn('class="citizen"', template)
        self.assertNotIn('class="citizen-intro"', template)
        self.assertIn('grid-template-columns:repeat(3,minmax(0,1fr))', template)
        # 2026-09-14: The teleconsulta CTA no longer hides its label on hover.
        # The old rule `.campaign-cta--tele:hover .campaign-cta-label{opacity:0}`
        # and the sliding arrow animation are removed per the product owner's rule that button
        # labels never hide. The arrow is now permanent, after the label, via
        # Demo review 2026-11-15: the three buttons are ONE style — label
        # only, as wide as their text; the lone arrow on «Telemedicina» is gone.
        self.assertNotIn("campaign-cta--tele:hover .campaign-cta-label", template)
        self.assertNotIn('campaign-cta--tele:after', template)
        self.assertNotIn("\\2009→", template)
        self.assertIn('color:var(--btn-fg,#fff)', template)
        cta = re.search(r"\.campaign-cta\{[^}]*\}", template).group(0)
        self.assertIn("justify-self:start", cta)
        self.assertNotIn("min-width:140px", cta)
        self.assertNotIn(".campaign-cta{width:100%", template)
        self.assertNotIn(".campaign-cta{width:100%;min-width:0", template)
        self.assertIn("if (p.action && href)", listing)

    def test_home_search_shell_is_one_labelled_click_target(self):
        root = Path(__file__).resolve().parents[1]
        template = (root / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<label class="searchbox" for="q">', template)
        self.assertIn('$("#ejemplos").style.display = q ? "none" : "";', template)

    def test_emergency_number_is_rendered_as_a_phone_link(self):
        self.assertEqual(
            site_identity.get("emergency_number_link_html"),
            '<a class="stz-link" href="tel:112">112</a>')

    def test_telemedicine_explains_uses_and_limits(self):
        # No numbered «Cómo funciona» on the demo: its steps were not a real
        # sequence (demo review 2026-11-15, D16).
        html = site_news._page_telemedicine().decode()
        self.assertIn("Para qué sirve", html)
        self.assertIn("Cuándo necesitás atención presencial", html)
        self.assertNotIn("Cómo funciona", html)
        self.assertIn("No es para emergencias", html)

    def test_zoonosis_marks_whatsapp_and_campaign_topics(self):
        html = site_news._page_zoonosis().decode()
        self.assertIn('class="wa-action"', html)
        self.assertIn("whatsapp-sf-regular.svg", html)
        self.assertIn("Castrar también es cuidar el barrio", html)
        self.assertIn("roedores", html)

    def test_blood_page_corrects_fasting_myth(self):
        html = site_news._page_blood_donation().decode()
        self.assertIn("No vayas en ayunas", html)
        self.assertIn("Llevá DNI", html)
        self.assertIn("Quiénes pueden donar", html)

    def test_announcement_can_target_a_service_or_cluster(self):
        service = {"id": "vacunatorio-pinamar", "cluster": "vacunas-prevencion"}
        rows = [{"title": "Aviso de vacunas", "body": "Consultá disponibilidad.",
                 "cluster_ids": ["vacunas-prevencion"]}]
        with mock.patch.object(site_preparation, "_announcements", return_value=rows):
            html = site_preparation._service_announcements_html(service)
        self.assertIn("Aviso de vacunas", html)
        self.assertIn("Consultá disponibilidad", html)

    def test_announcement_can_target_an_informational_link(self):
        rows = [{"title": "Vacuna antirrábica sin stock",
                 "body": "Consultá por WhatsApp antes de acercarte.",
                 "link_ids": ["zoonosis"]}]
        with mock.patch.object(site_preparation, "_announcements", return_value=rows):
            html = site_preparation._link_announcements_html("zoonosis")
        self.assertIn("Vacuna antirrábica sin stock", html)
        self.assertIn("Consultá por WhatsApp", html)
        self.assertEqual(site_preparation._link_announcements_html("telemedicina"), "")

    def test_inactive_announcement_is_not_loaded(self):
        with mock.patch.object(site_base, "_load", return_value={
                "announcements": [{"active": False, "body": "Borrador"}]}):
            self.assertEqual(site_preparation._announcements(), [])


if __name__ == "__main__":
    unittest.main()
